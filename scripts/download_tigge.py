#!/usr/bin/env python
"""Download TIGGE NCEP ensemble forecasts from the ECMWF Data Store (ECDS).

One request per (parameter group, forecast type, month), written to
data/raw/tigge_ncep/ with the exact request recorded in a manifest.

Why this script has three phases
--------------------------------
TIGGE is served from ECMWF's tape archive and ECDS progresses one job at a
time per account, so a request costs queue and tape time rather than
bandwidth: observed server-side waits for one month of NCEP precipitation
(about 1.5 MB) range from 7 minutes to 18 h 44 min.

An earlier version of this script used `cdsapi.Client.retrieve()`, which
submits, waits and downloads in one blocking call. On a long wait over an
unreliable link that loses data three ways: the wait is forfeited when the
link drops, the stranded job keeps occupying the account's single slot and
blocks everything behind it, and a job that finishes while nothing is
listening has its results expire unrecoverably. All three happened.

So submission is now separated from collection:

    submit    queue every outstanding month at once, persist the job ids
    collect   poll, download whatever is ready, verify and record it
    status    report what is queued, downloaded and outstanding

Submitting everything up front lets the queue drain while nothing is
connected, which turns total time from the sum of the waits into roughly the
longest one. Because job ids are persisted, collection can be interrupted and
resumed as often as needed, and a job stranded by an older blocking run can be
adopted by id instead of being queued again.

Before anything works you must accept the TIGGE licence once, signed in at
https://ecds.ecmwf.int/datasets/tigge-forecasts?tab=download

Credentials: ECDS_URL and ECDS_PAT in .env. Nothing is stored in code.

Usage:
    python scripts/download_tigge.py status
    python scripts/download_tigge.py submit --all --group precip --dry-run
    python scripts/download_tigge.py submit --all --group precip
    python scripts/download_tigge.py collect            # until everything lands
    python scripts/download_tigge.py collect --once     # one pass, then exit
    python scripts/download_tigge.py adopt <job_id> --target precip_pf_2023_07.grib
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import (  # noqa: E402
    SeasonMonth,
    load_config,
    load_dotenv_if_present,
    season_months,
)
from src.core.ecds import (  # noqa: E402
    DEFAULT_MAX_IN_FLIGHT,
    DONE_STATE,
    LIVE_STATES,
    EcdsClient,
    EcdsError,
    JobStore,
    wait_states,
)
from src.core.manifest import Manifest, setup_logging  # noqa: E402

JOB_STORE_NAME = "tigge_jobs.json"
SHORT = {"perturbed_forecast": "pf", "control_forecast": "cf"}


# --- request building (unchanged contract: tests/test_download_requests.py) --

def param_groups(cfg) -> dict:
    """Map a group name to its parameter-group definition from the config."""
    params = cfg["forecast"]["params"]
    return {
        "precip": params["precipitation"],
        "surface": params["surface"],
        "pressure": params["pressure_level"],
    }


def build_request(cfg, month: SeasonMonth, group: dict, fc_type: str) -> dict:
    """One ECDS request covering a month for one parameter group.

    Every value comes from configs/project.yaml rather than being written
    inline, because changing one of them changes the science.
    """
    fc = cfg["forecast"]
    r = cfg["region"]

    request = {
        "origin": fc["origin"],
        "level_type": group["level_type"],
        "variable": list(group["variables"]),
        "forecast_type": fc_type,
        "year": "%d" % month.year,
        "month": "%02d" % month.month,
        "day": ["%02d" % d for d in range(1, month.n_days + 1)],
        "time": fc["issue_time_utc"],
        "leadtime_hour": [str(s) for s in fc["steps_h"]],
        "data_format": fc["data_format"],
        # ECDS geographic extent is [north, west, south, east].
        "area": [r["north"], r["west"], r["south"], r["east"]],
    }

    if group["level_type"] == "pressure":
        request["level_value"] = list(group["level_value"])

    return request


def target_path(cfg, group_name: str, fc_type: str, month: SeasonMonth) -> Path:
    out_dir = cfg.get_path("raw_tigge")
    short = SHORT.get(fc_type, fc_type)
    name = "%s_%s_%d_%02d.grib" % (group_name, short, month.year, month.month)
    return out_dir / name


def manifest_id_for(group_name: str, fc_type: str, month: SeasonMonth) -> str:
    return "tigge_%s_%s_%d_%02d" % (group_name, SHORT.get(fc_type, fc_type),
                                    month.year, month.month)


# Which forecast types each parameter group is fetched for.
#
# Only precipitation gets the full perturbed ensemble. The atmospheric context
# groups are control-only, decided on 2026-10-16 (docs/decisions.md,
# "Atmospheric features come from the control forecast only"): a perturbed
# surface request is ~16,700 fields per month against ~560 for the control, so
# ensembles for every variable would cost roughly 13.5 h instead of 0.5 h.
# CLAUDE.md section 7.2 asks for these fields as context and never asks for
# their spread, so nothing in the feature set is lost.
#
# This lives here rather than behind the --type flag because passing
# `--group all --type both` would silently reinstate that 6x cost.
GROUP_TYPES = {
    "precip": ("perturbed", "control"),
    "surface": ("control",),
    "pressure": ("control",),
}

GROUP_ORDER = {"precip": 0, "surface": 1, "pressure": 2}
TYPE_ORDER = {"perturbed": 0, "control": 1}

ATMOSPHERIC = ("surface", "pressure")


def years_with_complete_precip(cfg, months) -> set[int]:
    """Years whose precipitation is already fully on disk.

    For such a year the only thing missing is atmospheric context, so a
    handful of cheap control requests finishes the season outright.
    """
    by_year: dict[int, list[bool]] = {}
    for month in months:
        for short in ("pf", "cf"):
            name = "precip_%s_%d_%02d.grib" % (short, month.year, month.month)
            path = cfg.get_path("raw_tigge") / name
            ok = path.exists() and path.stat().st_size > 0
            by_year.setdefault(month.year, []).append(ok)
    return {y for y, flags in by_year.items() if all(flags)}


def work_priority(group_name: str, fc_type: str, month, complete_years: set[int]):
    """Sort key deciding what to ask ECDS for next.

    ECDS runs only one TIGGE job at a time per account during heavy usage and
    jobs can hang for over a day (confirmed on the ECMWF forum), so the queue
    may deliver only a handful more files. The order therefore maximises the
    number of *fully usable* seasons rather than the number of files.

    Tier 0  atmospheric context for a year whose precipitation is already
            complete. These are the cheap control requests (~560 and ~280
            fields per month), and each one moves a season from "labels only"
            to "every feature in CLAUDE.md 7.2", which is what makes the
            section 7.3 ablation possible.
    Tier 1  precipitation for the remaining years. Expensive 30-member
            requests, but nothing can be built for a season without them.
    Tier 2  atmospheric context for years whose precipitation is still
            missing - worth nothing until that precipitation arrives.

    Chosen with the user on 2026-10-24: a smaller complete study beats a
    larger one that cannot answer the research question.
    """
    atmospheric = group_name in ATMOSPHERIC
    if atmospheric and month.year in complete_years:
        tier = 0
    elif group_name == "precip":
        tier = 1
    else:
        tier = 2

    short_key = "perturbed" if fc_type.startswith("perturbed") else "control"
    return (tier, month.year, month.month,
            GROUP_ORDER.get(group_name, 99), TYPE_ORDER.get(short_key, 99))


def forecast_types(cfg, which: str, group_name: str | None = None) -> list[str]:
    """Forecast types to fetch, honouring the per-group decision above."""
    types_cfg = cfg["forecast"]["forecast_types"]
    allowed = GROUP_TYPES.get(group_name, ("perturbed", "control"))

    if which == "both":
        keys = [k for k in ("perturbed", "control") if k in allowed]
        if "control" in keys and not cfg["forecast"]["members"]["include_control"]:
            keys.remove("control")
        return [types_cfg[k] for k in keys]

    key = "perturbed" if which == "pf" else "control"
    # An explicit --type that a group does not take yields nothing for that
    # group, rather than quietly fetching the expensive variant.
    return [types_cfg[key]] if key in allowed else []


def planned_work(cfg, months, groups, selected, types_arg):
    """Every (group, type, month) the configuration asks for.

    Ordered group-major so that precipitation is requested before the
    atmospheric groups, since the labels depend on it.
    """
    complete_years = years_with_complete_precip(cfg, months)
    plan = []
    for group_name in selected:
        for fc_type in forecast_types(cfg, types_arg, group_name):
            for month in months:
                plan.append((
                    work_priority(group_name, fc_type, month, complete_years),
                    group_name, groups[group_name], fc_type, month,
                ))
    plan.sort(key=lambda row: row[0])
    for row in plan:
        yield row[1], row[2], row[3], row[4]


# --- submit -----------------------------------------------------------------

def outstanding_work(cfg, store, months, selected, types, groups):
    """Every target still needing a file, with the request that would fill it.

    A target is outstanding when there is no complete file on disk and no job
    currently queued, running or awaiting download for it. Quota-rejected
    entries are outstanding again, since a rejection says nothing about the
    request.
    """
    out = []
    for group_name, group, fc_type, month in planned_work(
            cfg, months, groups, selected, types):
        target = target_path(cfg, group_name, fc_type, month)

        # data/raw is append-only: never re-request what is already on disk.
        if target.exists() and target.stat().st_size > 0:
            continue

        entry = store.get(target.name)
        if entry and entry.get("status") in LIVE_STATES + (DONE_STATE, "complete"):
            continue

        out.append((target.name, build_request(cfg, month, group, fc_type)))
    return out


def fill_slots(cfg, client, store, logger, outstanding, max_in_flight,
               dry_run: bool) -> tuple[int, int]:
    """Top the queue up to `max_in_flight`, newest submissions last.

    Returns (submitted, rejected_on_submit). Submitting only up to the cap is
    what keeps jobs from being rejected: over the quota ECDS accepts the
    request and then discards it, which wastes the slot and hides the real
    reason behind a bare "rejected" state.
    """
    submitted = failed = 0
    free = max_in_flight - store.n_in_flight()
    if free <= 0:
        return 0, 0

    for target_name, request in outstanding[:free]:
        logger.info("submit %s: %s", target_name,
                    {k: v for k, v in request.items() if k != "day"})
        if dry_run:
            submitted += 1
            continue

        try:
            job_id = client.submit(cfg["forecast"]["dataset"], request)
        except EcdsError as exc:
            logger.error("%s: submit failed: %s", target_name, exc)
            failed += 1
            continue

        if store.get(target_name):
            store.resubmit(target_name, job_id)
        else:
            store.add(target_name, job_id, cfg["forecast"]["dataset"], request)
        store.save()
        submitted += 1
        logger.info("%s: queued as %s (%d/%d in flight)", target_name, job_id,
                    store.n_in_flight(), max_in_flight)

    return submitted, failed


# --- collect ----------------------------------------------------------------

def verify_downloaded(cfg, target: Path, entry: dict, logger) -> dict:
    """Read the GRIB back and check it is the month and shape we asked for.

    The job store records what was *requested*; only the file says what
    actually arrived. This matters most for adopted jobs, whose request
    parameters were inferred from submission logs rather than read from the
    API, which does not expose them.
    """
    from src.core import io_tigge

    result: dict = {"verified": False}
    try:
        da, qc = io_tigge.load_file(target, cfg)
    except Exception as exc:
        result["error"] = "could not read back: %s" % exc
        logger.error("%s: %s", target.name, result["error"])
        return result

    import numpy as np
    import pandas as pd

    times = pd.to_datetime(np.atleast_1d(da.time.values))
    years = sorted({int(t.year) for t in times})
    months = sorted({int(t.month) for t in times})
    result.update({
        "n_members": qc.n_members,
        "n_issue_dates": len(times),
        "years_in_file": years,
        "months_in_file": months,
        "grid": list(qc.shape),
        "n_negative_clipped": qc.n_negative_clipped,
        "value_max_mm": round(qc.value_max, 3),
    })

    want_year = int(entry["request"]["year"])
    want_month = int(entry["request"]["month"])
    if years != [want_year] or months != [want_month]:
        result["error"] = (
            "contents are %s/%s but this file is named for %04d-%02d"
            % (years, months, want_year, want_month))
        logger.error("%s: %s", target.name, result["error"])
        return result

    expected_members = (cfg["forecast"]["members"]["perturbed"]
                        if entry["request"]["forecast_type"] == "perturbed_forecast"
                        else 1)
    if qc.n_members != expected_members:
        # Not fatal: CLAUDE.md 5.2 says to record a changed ensemble size
        # rather than reject it, and to use member fractions downstream.
        result["member_count_differs"] = True
        logger.warning("%s: %d members, configuration expects %d - recorded, "
                       "use member fractions not counts",
                       target.name, qc.n_members, expected_members)

    result["verified"] = True
    logger.info("%s: verified %d members, %d issue dates, grid %s, max %.1f mm",
                target.name, qc.n_members, len(times), list(qc.shape),
                qc.value_max)
    return result


def write_manifest(cfg, target: Path, entry: dict, elapsed: float | None,
                   checks: dict) -> Path:
    """Record one delivered file, including how long ECDS took."""
    request = entry["request"]
    name = target.stem                       # e.g. precip_pf_2023_07
    group_name, short, year, month = name.split("_")
    fc_type = request.get("forecast_type", "")

    manifest = Manifest(
        manifest_id="tigge_%s_%s_%s_%s" % (group_name, short, year, month),
        source="ECMWF Data Store (ecds.ecmwf.int)",
        product="TIGGE " + cfg["forecast"]["origin"],
        version=entry.get("dataset", cfg["forecast"]["dataset"]),
        request=request,
    )
    manifest.set("forecast_type", fc_type)
    manifest.set("requested_members",
                 cfg["forecast"]["members"]["perturbed"]
                 if fc_type == "perturbed_forecast" else 1)
    manifest.set("steps_h", cfg["forecast"]["steps_h"])
    manifest.set("ecds_job_id", entry["job_id"])
    manifest.set("ecds_submitted_utc", entry.get("submitted_utc"))
    manifest.set("ecds_queue_seconds",
                 round(elapsed, 1) if elapsed is not None else None)
    manifest.set("adopted_job", bool(entry.get("adopted")))
    manifest.set("verification", checks)

    manifest.add_file(target)

    if entry.get("adopted"):
        manifest.add_issue(
            "adopted_job",
            "job was submitted by an earlier interrupted run; its request "
            "parameters were inferred from submission logs and then confirmed "
            "against the file contents")
    if not checks.get("verified"):
        manifest.add_issue("verification_failed",
                           checks.get("error", "unknown verification failure"))
    if checks.get("member_count_differs"):
        manifest.add_issue(
            "member_count_differs",
            "file has %s members, configuration expects %s; use member "
            "fractions rather than counts (CLAUDE.md 5.2)"
            % (checks.get("n_members"),
               cfg["forecast"]["members"]["perturbed"]))
    if checks.get("n_negative_clipped"):
        manifest.add_issue(
            "negative_differences_clipped",
            "%d negative step differences clipped to zero (rounding in the "
            "accumulated field)" % checks["n_negative_clipped"])

    return manifest.write()


def collect_once(cfg, client, store, logger) -> dict:
    """Refresh states, then download and record everything that is ready."""
    wait_states(client, store, logger)

    counts = {"downloaded": 0, "failed": 0, "dead": 0}

    for name, entry in list(store.pending_download().items()):
        target = cfg.get_path("raw_tigge") / name
        if target.exists() and target.stat().st_size > 0:
            entry["downloaded_utc"] = entry.get("downloaded_utc") or "pre-existing"
            store.save()
            continue

        try:
            href, size = client.result_href(entry["job_id"])
            t0 = time.time()
            written = client.download(href, target, expect_bytes=size)
        except EcdsError as exc:
            # "expired" here means the tape wait has to be paid again.
            logger.error("%s: download failed: %s", name, exc)
            entry["status"] = "expired" if "expired" in str(exc).lower() \
                else "download_failed"
            entry.setdefault("history", []).append(
                {"state": entry["status"], "detail": str(exc)[:300]})
            store.save()
            counts["failed"] += 1
            continue

        logger.info("%s: %.2f MB downloaded in %.1f s",
                    name, written / 1e6, time.time() - t0)

        checks = verify_downloaded(cfg, target, entry, logger)
        entry["verification"] = checks
        entry["request_verified"] = bool(checks.get("verified"))
        entry["downloaded_utc"] = None

        if not checks.get("verified"):
            # Keep the bytes (they cost hours) but do not let a mislabelled
            # file enter data/raw under a name that claims the wrong month.
            quarantine = target.with_suffix(".grib.unverified")
            target.replace(quarantine)
            logger.error("%s: moved to %s pending investigation",
                         name, quarantine.name)
            entry["status"] = "unverified"
            store.save()
            counts["failed"] += 1
            continue

        from src.core.manifest import utc_now_iso
        entry["downloaded_utc"] = utc_now_iso()
        entry["status"] = "complete"

        elapsed = None
        try:
            from datetime import datetime
            t_sub = datetime.fromisoformat(
                entry["submitted_utc"].replace("Z", "+00:00"))
            t_dl = datetime.fromisoformat(
                entry["downloaded_utc"].replace("Z", "+00:00"))
            elapsed = (t_dl - t_sub).total_seconds()
        except Exception:
            pass

        path = write_manifest(cfg, target, entry, elapsed, checks)
        entry["manifest"] = path.name
        store.save()
        counts["downloaded"] += 1

    # These go back into the outstanding list and are resubmitted by the main
    # loop as soon as a slot frees, so this is a progress note, not a problem.
    for name, entry in store.needs_resubmit().items():
        logger.info("%s: %s after %d attempt(s) - will requeue when a slot "
                    "frees", name, entry["status"], entry.get("attempts", 1))
        counts["dead"] += 1

    return counts


def run_loop(cfg, client, store, logger, months, selected, types, groups,
             interval: int, once: bool, max_in_flight: int) -> int:
    """Keep the queue full and collect results until every month has landed.

    One loop does both jobs because they are coupled: a slot only frees when a
    job finishes, so the moment a download completes is exactly the moment the
    next request can be submitted. Running submit once and collect separately
    would leave the queue under-filled for hours at a time.
    """
    passes = 0
    consecutive_errors = 0
    while True:
        passes += 1

        # A pass must never end the run. The job ids are on disk, so the right
        # response to any transient failure - DNS, a 502, a dropped socket -
        # is to log it and poll again; the queue keeps working server-side
        # while we are blind. An earlier collector was killed by one
        # unhandled DNS lookup failure after 40 minutes of polling.
        try:
            counts = collect_once(cfg, client, store, logger)
            outstanding = outstanding_work(cfg, store, months, selected, types,
                                           groups)
            submitted, submit_failed = fill_slots(
                cfg, client, store, logger, outstanding, max_in_flight, False)
            consecutive_errors = 0
        except KeyboardInterrupt:
            logger.info("interrupted; %d job(s) remain queued at ECDS and are "
                        "recorded in %s", store.n_in_flight(), store.path)
            return 0
        except Exception as exc:
            consecutive_errors += 1
            logger.error("pass %d failed (%s): %s", passes,
                         type(exc).__name__, exc)
            if consecutive_errors >= 30:
                logger.error("%d consecutive failed passes; giving up. "
                             "Queued jobs are recorded in %s and can be "
                             "collected later.", consecutive_errors, store.path)
                return 1
            # Back off a little, but keep polling: the network usually returns.
            time.sleep(min(interval, 60 * consecutive_errors))
            continue

        live = store.live()
        pending = store.pending_download()
        done = len(store.complete())

        if counts["downloaded"]:
            logger.info("collected %d file(s) this pass", counts["downloaded"])

        # Finished only when nothing is in flight, nothing waits to download,
        # and nothing is left to submit.
        if not live and not pending and not outstanding:
            logger.info("all done. complete: %d, passes: %d", done, passes)
            return 0

        if once:
            logger.info("one pass done. complete: %d, in flight: %d, "
                        "ready to download: %d, outstanding: %d",
                        done, len(live), len(pending),
                        max(0, len(outstanding) - submitted))
            return 0

        if not live and not pending and outstanding and submit_failed:
            logger.error("cannot make progress: %d outstanding, every submit "
                         "failed", len(outstanding))
            return 1

        states: dict[str, int] = {}
        for e in live.values():
            states[e["status"]] = states.get(e["status"], 0) + 1
        logger.info("complete %d/%d | in flight %s | outstanding %d | "
                    "waiting %ds",
                    done, done + len(live) + len(pending) + len(outstanding)
                    - submitted,
                    states or "none",
                    max(0, len(outstanding) - submitted), interval)
        time.sleep(interval)


# --- status -----------------------------------------------------------------

def report_status(cfg, store, logger, months, selected, types, groups) -> int:
    on_disk, queued, missing = [], [], []
    groups_map = groups

    for group_name, _group, fc_type, month in planned_work(
            cfg, months, groups_map, selected, types):
        target = target_path(cfg, group_name, fc_type, month)
        entry = store.get(target.name)
        if target.exists() and target.stat().st_size > 0:
            on_disk.append((target.name, target.stat().st_size))
        elif entry and entry.get("status") in LIVE_STATES + (DONE_STATE,):
            queued.append((target.name, entry["job_id"][:8], entry["status"]))
        else:
            missing.append((target.name,
                            entry.get("status") if entry else "not submitted"))

    logger.info("on disk: %d", len(on_disk))
    logger.info("queued at ECDS: %d", len(queued))
    for name, jid, state in queued:
        logger.info("    %-26s %s  %s", name, jid, state)
    logger.info("outstanding: %d", len(missing))
    for name, state in missing:
        logger.info("    %-26s %s", name, state)

    return 0 if not missing else 1


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["submit", "collect", "status", "adopt"],
                    help="submit queues requests; collect downloads results")
    ap.add_argument("job_id", nargs="?", help="adopt: the ECDS job id")
    ap.add_argument("--target", help="adopt: target file the job fills")
    ap.add_argument("--year", type=int, help="single year")
    ap.add_argument("--month", type=int, help="single month (needs --year)")
    ap.add_argument("--all", action="store_true",
                    help="every configured JJAS month")
    ap.add_argument("--group", default="precip",
                    choices=["precip", "surface", "pressure", "all"],
                    help="parameter group (precipitation first, per CLAUDE.md 5.2)")
    ap.add_argument("--type", default="both", choices=["pf", "cf", "both"],
                    help="perturbed members, control run, or both")
    ap.add_argument("--interval", type=int, default=120,
                    help="collect: seconds between polls")
    ap.add_argument("--max-in-flight", type=int, default=DEFAULT_MAX_IN_FLIGHT,
                    help="most jobs to keep queued at ECDS at once; over the "
                         "account quota further jobs are silently rejected")
    ap.add_argument("--once", action="store_true",
                    help="collect: a single pass instead of waiting")
    ap.add_argument("--dry-run", action="store_true",
                    help="submit: print the requests without sending them")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("download_tigge")
    load_dotenv_if_present()

    store = JobStore.load(cfg.get_path("manifests") / JOB_STORE_NAME)

    if args.all or args.phase in ("collect",):
        months = list(season_months(cfg))
    elif args.year and args.month:
        months = [SeasonMonth(args.year, args.month)]
    elif args.year:
        months = list(season_months(cfg, [args.year]))
    elif args.phase in ("status", "adopt"):
        months = list(season_months(cfg))
    else:
        ap.error("give --year [--month] or --all")

    groups = param_groups(cfg)
    selected = list(groups) if args.group == "all" else [args.group]
    # Passed through as the raw flag: the forecast types are resolved per
    # group, because the atmospheric groups are control-only (see GROUP_TYPES).
    types = args.type

    r = cfg["region"]
    logger.info("phase: %s", args.phase)
    logger.info("dataset: %s   origin: %s", cfg["forecast"]["dataset"],
                cfg["forecast"]["origin"])
    logger.info("area [N,W,S,E]: %s", [r["north"], r["west"], r["south"], r["east"]])
    logger.info("months: %d, groups: %s", len(months), selected)
    for group_name in selected:
        logger.info("    %-9s -> %s", group_name,
                    forecast_types(cfg, types, group_name))
    logger.info("job store: %s (%d entries)", store.path, len(store.jobs))

    if args.phase == "status":
        return report_status(cfg, store, logger, months, selected, types, groups)

    if args.phase == "adopt":
        if not args.job_id or not args.target:
            ap.error("adopt needs a job id and --target")
        name = Path(args.target).name
        group_name, short, year, month = Path(name).stem.split("_")
        fc_type = {"pf": "perturbed_forecast", "cf": "control_forecast"}[short]
        request = build_request(cfg, SeasonMonth(int(year), int(month)),
                                groups[group_name], fc_type)
        store.add(name, args.job_id, cfg["forecast"]["dataset"], request,
                  adopted=True,
                  note="submitted by an earlier interrupted run; request "
                       "inferred from submission logs, confirmed on download")
        store.save()
        logger.info("adopted %s as %s (request inferred, will be verified "
                    "against the file)", args.job_id, name)
        return 0

    client = None
    if not args.dry_run:
        try:
            client = EcdsClient()
        except EcdsError as exc:
            logger.error("%s", exc)
            return 1
        accepted = client.licence_accepted()
        if accepted is False:
            logger.error("the TIGGE licence has not been accepted on this account")
            logger.error("accept it once at https://ecds.ecmwf.int/datasets/"
                         "tigge-forecasts?tab=download")
            return 1
        if accepted is None:
            logger.warning("could not check licence acceptance; continuing")
        else:
            logger.info("TIGGE licence accepted on this account")

    if args.phase == "submit":
        outstanding = outstanding_work(cfg, store, months, selected, types,
                                       groups)
        logger.info("outstanding: %d, in flight: %d, cap: %d",
                    len(outstanding), store.n_in_flight(), args.max_in_flight)
        submitted, failed = fill_slots(cfg, client, store, logger, outstanding,
                                       args.max_in_flight, args.dry_run)
        logger.info("submitted %d, submit failures %d, still outstanding %d",
                    submitted, failed, max(0, len(outstanding) - submitted))
        if not args.dry_run:
            logger.info("now run: python scripts/download_tigge.py collect "
                        "(it keeps the queue topped up as slots free)")
        return 1 if failed else 0

    return run_loop(cfg, client, store, logger, months, selected, types,
                    groups, args.interval, args.once, args.max_in_flight)


if __name__ == "__main__":
    raise SystemExit(main())
