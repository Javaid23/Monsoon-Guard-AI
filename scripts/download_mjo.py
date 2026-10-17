#!/usr/bin/env python
"""Download the MJO indices (BoM RMM and NOAA PSL OMI) and check their coverage.

Both files are updated in place at the source, so each download is stored under
a date-stamped name in data/raw/mjo/. That keeps data/raw append-only and makes
it possible to say exactly which version of the index a forecast record used.

The script parses what it downloads only far enough to report coverage, missing
values and the last available date. Real parsing lives in src/core/io_mjo.py.

Usage:
    python scripts/download_mjo.py                 # both indices
    python scripts/download_mjo.py --index rmm
    python scripts/download_mjo.py --check-only    # report on what is already here
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import load_config, season_months  # noqa: E402
from src.core.manifest import Manifest, setup_logging  # noqa: E402

# RMM uses large sentinel values where a value is missing (CLAUDE.md 5.3).
SENTINEL_MIN = 999.0

# The BoM server returns 403 to the default requests user agent.
HEADERS = {"User-Agent": "MonsoonGuardAI/0.1 (research; +hackathon project)"}


def download(url: str, dest: Path, logger) -> Path:
    import requests

    logger.info("fetching %s", url)
    resp = requests.get(url, timeout=120, headers=HEADERS)
    resp.raise_for_status()
    partial = dest.with_suffix(dest.suffix + ".part")
    partial.write_bytes(resp.content)
    partial.replace(dest)
    logger.info("wrote %s (%.2f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


# --- parsing for coverage checks only --------------------------------------

def parse_rmm(path: Path) -> dict:
    """Read the BoM RMM file far enough to report coverage and missing values.

    Columns: year, month, day, RMM1, RMM2, phase, amplitude, source note.
    The first two lines are a header.
    """
    rows, bad_lines, n_missing = [], 0, 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            parts = line.split()
            if i < 2 or len(parts) < 7:
                continue
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                rmm1, rmm2 = float(parts[3]), float(parts[4])
                phase, amp = int(parts[5]), float(parts[6])
            except ValueError:
                bad_lines += 1
                continue
            missing = any(abs(v) >= SENTINEL_MIN for v in (rmm1, rmm2, amp))
            if missing:
                n_missing += 1
            rows.append((date(y, m, d), missing))
    return {
        "n_rows": len(rows),
        "bad_lines": bad_lines,
        "n_missing": n_missing,
        "first_date": rows[0][0].isoformat() if rows else None,
        "last_date": rows[-1][0].isoformat() if rows else None,
        "dates": {r[0] for r in rows if not r[1]},
    }


def parse_omi(path: Path) -> dict:
    """Read the NOAA PSL OMI file.

    The file served at psl.noaa.gov/mjo/mjoindex/omi.1x.txt has six columns:
    year, month, day, pc1, pc2, amplitude. Some OMI distributions carry an
    extra hour column in position 4, so both widths are handled.
    """
    rows, bad_lines, n_missing = [], 0, 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 6:
                continue
            offset = 3 if len(parts) == 6 else 4   # skip an hour column if present
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                pc1 = float(parts[offset])
                pc2 = float(parts[offset + 1])
                amp = float(parts[offset + 2])
            except (ValueError, IndexError):
                bad_lines += 1
                continue
            missing = any(abs(v) >= SENTINEL_MIN for v in (pc1, pc2, amp))
            if missing:
                n_missing += 1
            rows.append((date(y, m, d), missing))
    return {
        "n_rows": len(rows),
        "bad_lines": bad_lines,
        "n_missing": n_missing,
        "first_date": rows[0][0].isoformat() if rows else None,
        "last_date": rows[-1][0].isoformat() if rows else None,
        "dates": {r[0] for r in rows if not r[1]},
    }


def season_dates(cfg) -> set:
    """Every JJAS date the project needs an index value for."""
    from datetime import timedelta
    wanted = set()
    for month in season_months(cfg):
        day = month.start
        while day <= month.end:
            wanted.add(day)
            day += timedelta(days=1)
    return wanted


def check_coverage(cfg, stats: dict, label: str, manifest: Manifest, logger) -> bool:
    """Report whether the index actually covers the configured seasons."""
    logger.info("%s: %d rows, %s to %s, %d missing values, %d unparsed lines",
                label, stats["n_rows"], stats["first_date"], stats["last_date"],
                stats["n_missing"], stats["bad_lines"])

    manifest.set(label + "_rows", stats["n_rows"])
    manifest.set(label + "_first_date", stats["first_date"])
    manifest.set(label + "_last_date", stats["last_date"])
    manifest.set(label + "_missing_values", stats["n_missing"])

    wanted = season_dates(cfg)
    gaps = sorted(wanted - stats["dates"])
    manifest.set(label + "_season_days_required", len(wanted))
    manifest.set(label + "_season_days_present", len(wanted) - len(gaps))

    if not gaps:
        logger.info("%s: covers all %d required JJAS days", label, len(wanted))
        return True

    manifest.add_issue(
        label + "_coverage_gap",
        "%d of %d required JJAS days are missing (first %s, last %s)"
        % (len(gaps), len(wanted), gaps[0].isoformat(), gaps[-1].isoformat()),
        n_missing_days=len(gaps),
    )
    logger.error("%s: GAP - %d of %d required JJAS days missing (%s .. %s)",
                 label, len(gaps), len(wanted),
                 gaps[0].isoformat(), gaps[-1].isoformat())

    # Say which seasons are affected, since a stale file usually truncates
    # the most recent years - exactly the calibration and test years.
    by_year = {}
    for g in gaps:
        by_year[g.year] = by_year.get(g.year, 0) + 1
    for year in sorted(by_year):
        logger.error("  %d: %d days missing", year, by_year[year])
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default="both", choices=["rmm", "omi", "both"])
    ap.add_argument("--check-only", action="store_true",
                    help="do not download; check the newest local copy instead")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("download_mjo")
    out_dir = cfg.get_path("raw_mjo")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    manifest = Manifest(
        manifest_id="mjo_" + stamp,
        source="BoM (RMM) and NOAA PSL (OMI)",
        product="mjo_indices",
        version=stamp,
        request={"rmm_url": cfg["mjo"]["rmm_url"], "omi_url": cfg["mjo"]["omi_url"]},
    )

    targets = []
    if args.index in ("rmm", "both"):
        targets.append(("rmm", cfg["mjo"]["rmm_url"],
                        out_dir / ("rmm.74toRealtime_%s.txt" % stamp), parse_rmm))
    if args.index in ("omi", "both"):
        targets.append(("omi", cfg["mjo"]["omi_url"],
                        out_dir / ("omi_%s.txt" % stamp), parse_omi))

    all_ok, any_file = True, False
    for label, url, dest, parser in targets:
        if args.check_only:
            existing = sorted(out_dir.glob(label + "*.txt"))
            if not existing:
                logger.error("%s: no local copy in %s", label, out_dir)
                all_ok = False
                continue
            dest = existing[-1]
            logger.info("%s: checking newest local copy %s", label, dest.name)
        else:
            try:
                download(url, dest, logger)
            except Exception as exc:
                manifest.add_issue(label + "_download_failed", str(exc), url=url)
                logger.error("%s: download failed from %s: %s", label, url, exc)
                logger.error("  check the source page and update mjo.%s_url "
                             "in configs/project.yaml", label)
                all_ok = False
                continue

        any_file = True
        try:
            stats = parser(dest)
        except Exception as exc:
            manifest.add_issue(label + "_parse_failed", str(exc))
            logger.exception("%s: could not parse %s", label, dest.name)
            all_ok = False
            continue

        if stats["n_rows"] == 0:
            manifest.add_issue(label + "_empty", "no parsable rows in " + dest.name)
            logger.error("%s: no parsable rows - the file format may have changed",
                         label)
            all_ok = False
            continue

        manifest.add_file(dest, index=label, source_url=url)
        if not check_coverage(cfg, stats, label, manifest, logger):
            all_ok = False

    if any_file:
        path = manifest.write()
        logger.info("manifest: %s", path.name)

    if not all_ok:
        logger.error("one or more indices are incomplete or unavailable - "
                     "see the issues in the manifest before using them as features")
        return 1
    logger.info("all requested indices downloaded and cover the configured seasons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
