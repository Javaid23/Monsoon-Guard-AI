#!/usr/bin/env python
"""Download TIGGE NCEP ensemble forecasts from the ECMWF Data Store (ECDS).

One request per (parameter group, forecast type, month). Requests are queued by
ECDS and served asynchronously, so this script submits them one at a time,
writes each GRIB to data/raw/tigge_ncep/ and records the exact request in a
manifest.

ECDS replaced the apps.ecmwf.int Public Datasets service, which was
decommissioned on 2026-05-27. The MARS-style request in CLAUDE.md section 5.2
(class / expver / param / number / step / grid) no longer applies; ECDS uses
CDS-style keys. See docs/decisions.md.

Before anything works you must accept the TIGGE licence once, signed in at
https://ecds.ecmwf.int/datasets/tigge-forecasts?tab=download

Credentials: ECDS_URL and ECDS_PAT in .env (personal access token from
https://ecds.ecmwf.int/how-to-api). Nothing is stored in code.

Usage:
    python scripts/download_tigge.py --year 2022 --month 8 --dry-run
    python scripts/download_tigge.py --year 2022 --month 8 --group precip
    python scripts/download_tigge.py --all --group precip
    python scripts/download_tigge.py --all --group all
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import (  # noqa: E402
    SeasonMonth,
    get_credential,
    load_config,
    load_dotenv_if_present,
    season_months,
)
from src.core.manifest import Manifest, setup_logging  # noqa: E402


# --- auth -------------------------------------------------------------------

def make_client(logger):
    """Build a cdsapi client pointed at ECDS, using the token from .env."""
    try:
        import cdsapi
    except ImportError:
        raise SystemExit(
            "cdsapi is not installed.\n"
            "    pip install 'cdsapi>=0.7.7'"
        )

    load_dotenv_if_present()
    url = get_credential("ECDS_URL") or "https://ecds.ecmwf.int/api"
    pat = get_credential("ECDS_PAT")

    if not pat:
        raise SystemExit(
            "No ECDS token found.\n"
            "Edit the .env file in the project root and set\n"
            "  ECDS_URL=https://ecds.ecmwf.int/api\n"
            "  ECDS_PAT=<your personal access token>\n"
            "Get the token from https://ecds.ecmwf.int/how-to-api while signed in.\n"
            "You must also accept the TIGGE licence once at\n"
            "https://ecds.ecmwf.int/datasets/tigge-forecasts?tab=download"
        )

    logger.info("using ECDS at %s", url)
    return cdsapi.Client(url=url, key=pat, quiet=True, progress=False)


def check_licence(logger) -> bool:
    """Warn early if the TIGGE licence has not been accepted on this account."""
    import requests

    url = get_credential("ECDS_URL") or "https://ecds.ecmwf.int/api"
    pat = get_credential("ECDS_PAT")
    try:
        resp = requests.get(url + "/profiles/v1/account/licences",
                            headers={"PRIVATE-TOKEN": pat}, timeout=60)
        accepted = {lic["id"] for lic in resp.json().get("licences", [])}
    except Exception as exc:
        logger.warning("could not check licence acceptance: %s", exc)
        return True

    if "tigge-licence" not in accepted:
        logger.error("the TIGGE licence has not been accepted on this account")
        logger.error("accept it once at "
                     "https://ecds.ecmwf.int/datasets/tigge-forecasts?tab=download")
        return False
    logger.info("TIGGE licence accepted on this account")
    return True


# --- request building -------------------------------------------------------

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
    short = {"perturbed_forecast": "pf", "control_forecast": "cf"}.get(fc_type, fc_type)
    name = "%s_%s_%d_%02d.grib" % (group_name, short, month.year, month.month)
    return out_dir / name


# --- retrieval --------------------------------------------------------------

def retrieve_one(client, cfg, group_name: str, group: dict, fc_type: str,
                 month: SeasonMonth, logger, dry_run: bool):
    """Submit one request and record it. Returns a Manifest, or None if skipped."""
    target = target_path(cfg, group_name, fc_type, month)

    # data/raw is append-only: never re-request or overwrite an existing file.
    if target.exists():
        logger.info("%s: already present (%.2f MB), skipping",
                    target.name, target.stat().st_size / 1e6)
        return None

    request = build_request(cfg, month, group, fc_type)

    manifest = Manifest(
        manifest_id="tigge_%s_%s_%d_%02d" % (
            group_name,
            {"perturbed_forecast": "pf", "control_forecast": "cf"}.get(fc_type, fc_type),
            month.year, month.month),
        source="ECMWF Data Store (ecds.ecmwf.int)",
        product="TIGGE " + cfg["forecast"]["origin"],
        version=cfg["forecast"]["dataset"],
        request=request,
    )
    manifest.set("forecast_type", fc_type)
    manifest.set("requested_members",
                 cfg["forecast"]["members"]["perturbed"]
                 if fc_type == "perturbed_forecast" else 1)
    manifest.set("steps_h", cfg["forecast"]["steps_h"])
    manifest.set("expected_days", month.n_days)

    logger.info("request: %s", {k: v for k, v in request.items() if k != "day"})
    if dry_run:
        logger.info("%s: dry run, not submitted", target.name)
        return manifest

    # Download to a .part name so an interrupted transfer never looks complete.
    partial = target.with_suffix(".grib.part")

    t0 = time.time()
    try:
        client.retrieve(cfg["forecast"]["dataset"], request, str(partial))
    except Exception as exc:
        partial.unlink(missing_ok=True)
        manifest.add_issue("retrieve_failed", str(exc))
        logger.error("%s: retrieval failed: %s", target.name, exc)
        return manifest

    elapsed = time.time() - t0

    if not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        manifest.add_issue("empty_result",
                           "no data returned - check the date range and licence")
        logger.error("%s: request returned no data", target.name)
        return manifest

    partial.replace(target)
    manifest.add_file(target, retrieve_seconds=round(elapsed, 1))
    logger.info("%s: %.2f MB in %.1f s", target.name,
                target.stat().st_size / 1e6, elapsed)

    # Member count, step ranges and grid resolution are verified when the file
    # is read (src/core/io_tigge.py). The manifest records what was requested;
    # the loader records what actually arrived.
    manifest.add_issue(
        "pending_verification",
        "member count, step ranges and grid resolution not yet verified "
        "against the file contents",
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, help="single year")
    ap.add_argument("--month", type=int, help="single month (needs --year)")
    ap.add_argument("--all", action="store_true",
                    help="every configured JJAS month")
    ap.add_argument("--group", default="precip",
                    choices=["precip", "surface", "pressure", "all"],
                    help="parameter group (precipitation first, per CLAUDE.md 5.2)")
    ap.add_argument("--type", default="both",
                    choices=["pf", "cf", "both"],
                    help="perturbed members, control run, or both")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the requests without submitting them")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("download_tigge")

    if args.all:
        months = list(season_months(cfg))
    elif args.year and args.month:
        months = [SeasonMonth(args.year, args.month)]
    elif args.year:
        months = list(season_months(cfg, [args.year]))
    else:
        ap.error("give --year [--month] or --all")

    groups = param_groups(cfg)
    selected = list(groups) if args.group == "all" else [args.group]

    types_cfg = cfg["forecast"]["forecast_types"]
    if args.type == "both":
        types = [types_cfg["perturbed"]]
        if cfg["forecast"]["members"]["include_control"]:
            types.append(types_cfg["control"])
    else:
        types = [types_cfg["perturbed" if args.type == "pf" else "control"]]

    r = cfg["region"]
    logger.info("dataset: %s   origin: %s", cfg["forecast"]["dataset"],
                cfg["forecast"]["origin"])
    logger.info("area [N,W,S,E]: %s", [r["north"], r["west"], r["south"], r["east"]])
    logger.info("months: %d, groups: %s, types: %s", len(months), selected, types)
    logger.info("total requests to submit: %d",
                len(months) * len(selected) * len(types))

    client = None
    if not args.dry_run:
        load_dotenv_if_present()
        if not check_licence(logger):
            return 1
        client = make_client(logger)

    written, skipped, failed = [], 0, []
    for month in months:
        for group_name in selected:
            for fc_type in types:
                label = "%s/%s/%s" % (month, group_name, fc_type)
                try:
                    manifest = retrieve_one(client, cfg, group_name,
                                            groups[group_name], fc_type,
                                            month, logger, args.dry_run)
                except Exception:
                    logger.exception("%s: failed", label)
                    failed.append(label)
                    continue

                if manifest is None:
                    skipped += 1
                    continue
                if args.dry_run:
                    continue

                path = manifest.write()
                written.append(path.name)
                blocking = [i for i in manifest.data["issues"]
                            if i["kind"] != "pending_verification"]
                if blocking:
                    failed.append(label)

    logger.info("done. manifests: %d, skipped (already present): %d, failed: %d",
                len(written), skipped, len(failed))
    if failed:
        logger.error("failed requests: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
