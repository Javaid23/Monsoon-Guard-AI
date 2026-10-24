#!/usr/bin/env python
"""Download GEFS control-run atmospheric context fields from NOAA's open archive.

Why a second forecast source
----------------------------
The atmospheric context fields in CLAUDE.md 7.2 (total column water, CAPE,
CIN, mean sea level pressure, 10 m wind, and 850 hPa u, v and specific
humidity) were never delivered by ECDS: 0 of 16 months for either the surface
or pressure-level group. ECDS serves TIGGE from tape, runs one job at a time
per account during heavy usage and had completed nothing for six hours, so
waiting was not a plan.

NOAA publishes the same model openly. TIGGE's "NCEP" ensemble *is* NCEP's
GEFS, and the archive at s3://noaa-gefs-pds carries it on the identical
0.5 degree grid from the same 00 UTC initialisation, with no queue, no account
and no credentials. So the atmospheric context comes from here while the
precipitation ensemble stays TIGGE, which keeps every ensemble and spread
feature on its original source.

Two substitutions are deliberate and must appear in the report:

  * total column water -> PWAT (precipitable water). PWAT is the vapour
    column only; TIGGE's total column water also includes cloud condensate.
    Close, not identical.
  * These are operational GEFS fields rather than the TIGGE archive copy.
    GEFS v12 has been operational since September 2020, so 2021-2024 is one
    consistent model version.

How it works
------------
Whole GEFS files are 50-100 MB because they are global. Each file has a `.idx`
sidecar listing every field's byte offset, so one field is fetched with an HTTP
Range request - a few hundred KB - and cropped to the region locally. Nothing
is subset server-side, which is why the crop happens here.

Fields are decoded with eccodes directly from the downloaded bytes, avoiding a
temporary file per field; at 9 fields x 3 lead times x 244 days that matters.

Usage:
    python scripts/download_gefs_atmos.py --years 2021 2022 --dry-run
    python scripts/download_gefs_atmos.py --years 2021 2022
    python scripts/download_gefs_atmos.py --years 2021 2022 --workers 12
"""

from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

from src.core.config import load_config, season_months  # noqa: E402
from src.core.manifest import Manifest, setup_logging  # noqa: E402

BUCKET = "https://noaa-gefs-pds.s3.amazonaws.com"
CONTROL = "gec00"

# (config variable name, GRIB shortName, GRIB level string, which file set)
# The level strings are matched against the `.idx` description exactly as NOAA
# writes them, so a change upstream shows up as a missing field rather than a
# silently wrong level.
FIELDS = [
    ("total_column_water", "PWAT", "entire atmosphere (considered as a single layer)", "a"),
    ("convective_available_potential_energy", "CAPE", "180-0 mb above ground", "a"),
    ("convective_inhibition", "CIN", "180-0 mb above ground", "a"),
    ("mean_sea_level_pressure", "PRMSL", "mean sea level", "a"),
    ("10_m_u_component_of_wind", "UGRD", "10 m above ground", "a"),
    ("10_m_v_component_of_wind", "VGRD", "10 m above ground", "a"),
    ("u_component_of_wind_850", "UGRD", "850 mb", "a"),
    ("v_component_of_wind_850", "VGRD", "850 mb", "a"),
    ("specific_humidity_850", "SPFH", "850 mb", "b"),
]

SET_DIR = {"a": ("pgrb2ap5", "pgrb2a"), "b": ("pgrb2bp5", "pgrb2b")}


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=5, connect=5, read=3, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]), raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=32))
    return s


def file_url(day: date, step: int, which: str) -> str:
    sub, stem = SET_DIR[which]
    return "%s/gefs.%s/00/atmos/%s/%s.t00z.%s.0p50.f%03d" % (
        BUCKET, day.strftime("%Y%m%d"), sub, CONTROL, stem, step)


@dataclass
class Index:
    """Byte ranges for the fields we want, parsed from a .idx sidecar."""
    ranges: dict[tuple[str, str], tuple[int, int | None]]

    @classmethod
    def parse(cls, text: str) -> "Index":
        rows = []
        for line in text.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 5:
                continue
            rows.append((int(parts[1]), parts[3], parts[4]))
        ranges = {}
        for i, (offset, name, level) in enumerate(rows):
            end = rows[i + 1][0] - 1 if i + 1 < len(rows) else None
            ranges[(name, level)] = (offset, end)
        return cls(ranges)


def crop_indices(ni: int, nj: int, lat0: float, lon0: float,
                 dlat: float, dlon: float, region) -> tuple[slice, slice]:
    """Row/column slices covering the region on a global regular grid.

    Derived from the message's own grid description rather than assumed, so a
    change in the published grid is caught instead of silently mis-cropping.
    """
    lats = lat0 + np.arange(nj) * dlat      # dlat is negative for N->S
    lons = lon0 + np.arange(ni) * dlon
    rows = np.flatnonzero((lats <= region["north"] + 1e-6)
                          & (lats >= region["south"] - 1e-6))
    cols = np.flatnonzero((lons >= region["west"] - 1e-6)
                          & (lons <= region["east"] + 1e-6))
    if rows.size == 0 or cols.size == 0:
        raise ValueError("region does not intersect the published grid")
    return slice(int(rows[0]), int(rows[-1]) + 1), slice(int(cols[0]), int(cols[-1]) + 1)


# ecCodes is not thread-safe: decoding from several worker threads at once
# killed the process natively (exit 9, no Python traceback). Fetching is the
# slow part anyway - a few hundred KB per field over the network - so the
# requests stay parallel and only the decode is serialised behind this lock.
_DECODE_LOCK = threading.Lock()


def decode_field(raw: bytes, region):
    """Decode one GRIB2 message and crop it, returning (values, lats, lons)."""
    import eccodes

    with _DECODE_LOCK:
        return _decode_locked(eccodes, raw, region)


def _decode_locked(eccodes, raw: bytes, region):
    gid = eccodes.codes_new_from_message(raw)
    try:
        ni = eccodes.codes_get(gid, "Ni")
        nj = eccodes.codes_get(gid, "Nj")
        lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        dlat = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
        dlon = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        scan_negative = eccodes.codes_get(gid, "jScansPositively") == 0
        if scan_negative:
            dlat = -abs(dlat)
        values = eccodes.codes_get_values(gid).reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)

    rsl, csl = crop_indices(ni, nj, lat0, lon0, dlat, dlon, region)
    lats = (lat0 + np.arange(nj) * dlat)[rsl]
    lons = (lon0 + np.arange(ni) * dlon)[csl]
    return values[rsl, csl], lats, lons


def fetch_day_step(session, day: date, step: int, region, logger):
    """Every wanted field for one issue date and lead time."""
    out = {}
    coords = None
    for which in ("a", "b"):
        wanted = [f for f in FIELDS if f[3] == which]
        if not wanted:
            continue
        url = file_url(day, step, which)
        r = session.get(url + ".idx", timeout=60)
        if r.status_code != 200:
            raise RuntimeError("no index for %s (HTTP %d)" % (url, r.status_code))
        index = Index.parse(r.text)

        for cfg_name, short, level, _ in wanted:
            key = (short, level)
            if key not in index.ranges:
                raise RuntimeError("%s: field %s at %r not in index"
                                   % (url, short, level))
            start, end = index.ranges[key]
            hdr = {"Range": "bytes=%d-%s" % (start, "" if end is None else end)}
            rr = session.get(url, headers=hdr, timeout=120)
            if rr.status_code not in (200, 206):
                raise RuntimeError("%s: range fetch HTTP %d" % (url, rr.status_code))
            vals, lats, lons = decode_field(rr.content, region)
            if coords is None:
                coords = (lats, lons)
            out[cfg_name] = vals
    return day, step, out, coords


def build_month(cfg, session, year: int, month: int, logger, workers: int,
                dry_run: bool):
    region = cfg["region"]
    steps = list(cfg["forecast"]["steps_h"])
    lead_days = list(cfg["forecast"]["lead_days"])

    out_dir = cfg.get_path("raw_gefs_atmos") if "raw_gefs_atmos" in cfg["paths"] \
        else cfg.get_path("raw_tigge").parent / "gefs_atmos"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("atmos_cf_%d_%02d.nc" % (year, month))

    if target.exists() and target.stat().st_size > 0:
        logger.info("%s: already present, skipping", target.name)
        return target, 0, []

    first = date(year, month, 1)
    last = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) \
        - timedelta(days=1)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]

    tasks = [(d, s) for d in days for s in steps]
    logger.info("%s: %d days x %d lead times = %d field groups",
                target.name, len(days), len(steps), len(tasks))
    if dry_run:
        logger.info("%s: dry run, nothing fetched (example URL: %s)",
                    target.name, file_url(days[0], steps[0], "a"))
        return target, 0, []

    results: dict[tuple[date, int], dict] = {}
    failures: list[str] = []
    coords_holder = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_day_step, session, d, s, region, logger): (d, s)
                   for d, s in tasks}
        done = 0
        for fut in as_completed(futures):
            d, s = futures[fut]
            try:
                day, step, vals, coords = fut.result()
                results[(day, step)] = vals
                if coords is not None and "c" not in coords_holder:
                    coords_holder["c"] = coords
            except Exception as exc:
                failures.append("%s f%03d: %s" % (d, s, exc))
            done += 1
            if done % 60 == 0:
                logger.info("%s: %d/%d field groups", target.name, done, len(tasks))

    if not results:
        logger.error("%s: nothing retrieved", target.name)
        return target, 0, failures

    lats, lons = coords_holder["c"]
    times = sorted({d for d, _ in results})
    data_vars = {}
    for cfg_name, _, _, _ in FIELDS:
        arr = np.full((len(times), len(steps), len(lats), len(lons)),
                      np.nan, dtype="float32")
        for ti, d in enumerate(times):
            for si, s in enumerate(steps):
                got = results.get((d, s))
                if got is not None and cfg_name in got:
                    arr[ti, si] = got[cfg_name]
        data_vars[cfg_name] = (("time", "lead_day", "latitude", "longitude"), arr)

    ds = xr.Dataset(
        data_vars,
        coords={"time": [np.datetime64(d) for d in times],
                "lead_day": lead_days,
                "latitude": lats.astype("float64"),
                "longitude": lons.astype("float64")},
        attrs={
            "source": "NOAA GEFS control forecast (gec00), s3://noaa-gefs-pds",
            "forecast_type": "control_forecast",
            "init_time_utc": "00:00",
            "grid_deg": "0.5",
            "note": ("Atmospheric context fields for CLAUDE.md 7.2. "
                     "total_column_water is GEFS PWAT (precipitable water, "
                     "vapour column only); TIGGE total column water also "
                     "includes cloud condensate."),
            "substituted_for": "TIGGE NCEP surface and pressure_level groups",
        },
    )
    # Values are instantaneous fields at each forecast valid time, matching
    # CLAUDE.md 7.2: part of the issued forecast, never a later observation.
    ds["lead_day"].attrs["description"] = "1 = f024, 2 = f048, 3 = f072"

    ds.to_netcdf(target)
    ds.close()
    logger.info("%s: wrote %.2f MB (%d issue dates, %d failures)",
                target.name, target.stat().st_size / 1e6, len(times),
                len(failures))
    return target, len(times), failures


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", required=True,
                    help="seasons to fetch, e.g. --years 2021 2022")
    ap.add_argument("--workers", type=int, default=10,
                    help="parallel range requests")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("download_gefs_atmos")
    session = make_session()

    months = [m for m in season_months(cfg, args.years)]
    logger.info("source: %s (control run %s)", BUCKET, CONTROL)
    logger.info("months: %d, fields: %d, workers: %d",
                len(months), len(FIELDS), args.workers)
    logger.info("region [N,W,S,E]: %s",
                [cfg["region"]["north"], cfg["region"]["west"],
                 cfg["region"]["south"], cfg["region"]["east"]])

    written, all_failures = [], []
    for m in months:
        try:
            target, n_times, failures = build_month(
                cfg, session, m.year, m.month, logger, args.workers,
                args.dry_run)
        except Exception:
            logger.exception("%s: failed", m)
            all_failures.append(str(m))
            continue

        all_failures.extend(failures)
        if args.dry_run or n_times == 0:
            continue

        manifest = Manifest(
            manifest_id="gefs_atmos_cf_%d_%02d" % (m.year, m.month),
            source="NOAA Open Data (s3://noaa-gefs-pds), anonymous HTTPS",
            product="GEFS control forecast, atmospheric context",
            version="operational GEFS v12, pgrb2a/pgrb2b 0p50",
            request={
                "control_member": CONTROL,
                "steps_h": list(cfg["forecast"]["steps_h"]),
                "fields": [f[0] for f in FIELDS],
                "grib_fields": ["%s @ %s" % (f[1], f[2]) for f in FIELDS],
                "area_nwse": [cfg["region"]["north"], cfg["region"]["west"],
                              cfg["region"]["south"], cfg["region"]["east"]],
                "access": "HTTP Range requests guided by .idx sidecars",
            },
        )
        manifest.set("issue_dates", n_times)
        manifest.set("substitutes", "TIGGE NCEP surface + pressure_level groups")
        manifest.add_issue(
            "variable_substitution",
            "total_column_water is GEFS PWAT (precipitable water): vapour "
            "column only, whereas TIGGE total column water includes cloud "
            "condensate")
        manifest.add_issue(
            "source_substitution",
            "operational GEFS rather than the TIGGE archive copy; same centre "
            "(NCEP), same 0.5 deg grid, same 00 UTC initialisation")
        if failures:
            manifest.add_issue("incomplete_fetch",
                               "%d field groups failed: %s"
                               % (len(failures), "; ".join(failures[:5])))
        manifest.add_file(target)
        manifest.write()
        written.append(target.name)

    logger.info("done. months written: %d, failures: %d",
                len(written), len(all_failures))
    if all_failures:
        logger.error("failures (first 10): %s", "; ".join(all_failures[:10]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
