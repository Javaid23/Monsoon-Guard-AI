#!/usr/bin/env python
"""Download GPM IMERG Final daily rainfall for the study box.

Each IMERG daily granule is global: 3600 x 1800 cells, ~34 MB. The study box is
76 x 66 cells, 0.077 percent of the globe. Two routes are available and both
produce bitwise-identical values (verified against the archive on 2026-10-16):

  opendap  (default)  Ask the GES DISC OPeNDAP server for just the index range
                      we need. ~48 kB and ~5 s per day; ~30 MB for 610 days.
  download            Fetch the whole 34 MB granule and crop locally, in small
                      batches, deleting each global file afterwards. ~138 s per
                      day; ~20.7 GB for 610 days. Kept as a fallback for when
                      OPeNDAP is unavailable.

Credentials come from EARTHDATA_USERNAME / EARTHDATA_PASSWORD in .env, or from
~/.netrc. Nothing is read from the command line and nothing is stored in code.

Usage:
    python scripts/download_imerg.py --year 2022 --month 8 --dry-run
    python scripts/download_imerg.py --year 2022 --month 8
    python scripts/download_imerg.py --all                     # 2021-2025 JJAS
    python scripts/download_imerg.py --all --route download    # fallback route
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import (  # noqa: E402
    SeasonMonth,
    crop_bbox_wsen,
    get_credential,
    load_config,
    load_dotenv_if_present,
    season_months,
)
from src.core.manifest import Manifest, setup_logging  # noqa: E402

VARIABLE = "precipitation"
# randomError is IMERG's own uncertainty estimate. It costs little to fetch and
# supports the "the truth is uncertain" limitation in CLAUDE.md section 15.
EXTRA_VARIABLES = ["randomError"]

# GES DISC OPeNDAP (Hyrax). Verified against the archive on 2026-10-16:
# a server-side subset returns bitwise-identical values to downloading the
# global granule and cropping locally, for ~650x fewer bytes.
OPENDAP_BASE = ("https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/"
                "GPM_3IMERGDF.07")

# IMERG V07 global grid: cell centres at 0.1 degree spacing.
IMERG_LAT0, IMERG_LON0, IMERG_STEP = -89.95, -179.95, 0.1
IMERG_NLAT, IMERG_NLON = 1800, 3600


# --- auth -------------------------------------------------------------------

def authenticate(logger):
    import earthaccess

    load_dotenv_if_present()
    user = get_credential("EARTHDATA_USERNAME")
    password = get_credential("EARTHDATA_PASSWORD")

    if user and password:
        # earthaccess reads these from the environment; re-export the cleaned
        # values so surrounding whitespace or quotes in .env cannot break login.
        os.environ["EARTHDATA_USERNAME"] = user
        os.environ["EARTHDATA_PASSWORD"] = password
        strategy = "environment"
    elif user or password:
        missing = "EARTHDATA_PASSWORD" if user else "EARTHDATA_USERNAME"
        raise SystemExit(
            "Earthdata credentials are incomplete: " + missing + " is empty "
            "in .env.\nFill in both values (no quotes, no spaces around the =)."
        )
    elif (Path.home() / ".netrc").exists() or (Path.home() / "_netrc").exists():
        strategy = "netrc"
    else:
        raise SystemExit(
            "No Earthdata credentials found.\n"
            "Edit the .env file in the project root and set\n"
            "  EARTHDATA_USERNAME=...\n"
            "  EARTHDATA_PASSWORD=...\n"
            "Register free at https://urs.earthdata.nasa.gov\n"
            "A ~/.netrc entry for urs.earthdata.nasa.gov works instead."
        )
    auth = earthaccess.login(strategy=strategy)
    if not getattr(auth, "authenticated", False):
        raise SystemExit("Earthdata login failed using strategy " + strategy)
    logger.info("authenticated with Earthdata (strategy=%s)", strategy)
    return auth


# --- search -----------------------------------------------------------------

def search_month(cfg, month: SeasonMonth, logger):
    import earthaccess

    bbox = crop_bbox_wsen(cfg)
    results = earthaccess.search_data(
        short_name=cfg["observations"]["product"],
        version=cfg["observations"]["version"],
        temporal=(month.start.isoformat(), month.end.isoformat()),
        bounding_box=bbox,
    )
    logger.info("%s: %d granules found (expected %d days)",
                month, len(results), month.n_days)
    return results


def granule_name(result) -> str:
    """Best-effort granule file name from an earthaccess result."""
    try:
        links = result.data_links()
        if links:
            return links[0].split("/")[-1].split("?")[0]
    except Exception:  # pragma: no cover - result shapes vary by version
        pass
    return str(result).split("/")[-1]


def granule_date(name: str) -> str | None:
    """Pull the YYYYMMDD stamp out of an IMERG granule file name."""
    for part in name.split("."):
        if part.startswith(("2", "1")) and "-S" in part:
            stamp = part.split("-")[0]
            if len(stamp) == 8 and stamp.isdigit():
                return stamp
    return None


# --- OPeNDAP server-side subsetting -----------------------------------------

def grid_indices(cfg) -> tuple[int, int, int, int]:
    """Index ranges on the IMERG global grid covering the crop box.

    Returns (lat0, lat1, lon0, lon1), all inclusive. The returned coordinates
    are checked against the crop box after every fetch, so an arithmetic slip
    here fails loudly rather than producing a quietly shifted grid.
    """
    import math

    c = cfg["region"]["crop"]
    eps = 1e-9
    lat0 = int(math.ceil((c["south"] - IMERG_LAT0) / IMERG_STEP - eps))
    lat1 = int(math.floor((c["north"] - IMERG_LAT0) / IMERG_STEP + eps))
    lon0 = int(math.ceil((c["west"] - IMERG_LON0) / IMERG_STEP - eps))
    lon1 = int(math.floor((c["east"] - IMERG_LON0) / IMERG_STEP + eps))

    if not (0 <= lat0 <= lat1 < IMERG_NLAT and 0 <= lon0 <= lon1 < IMERG_NLON):
        raise ValueError(
            "crop box falls outside the IMERG grid: lat %d..%d lon %d..%d"
            % (lat0, lat1, lon0, lon1))
    return lat0, lat1, lon0, lon1


def opendap_url(granule: str, cfg) -> str:
    """Full OPeNDAP subset URL for one granule.

    IMERG stores precipitation as (time, lon, lat), so the constraint
    expression lists the longitude range before the latitude range.
    """
    stamp = granule_date(granule)
    if stamp is None:
        raise ValueError("cannot read a date from granule name: " + granule)

    lat0, lat1, lon0, lon1 = grid_indices(cfg)
    span = "[0:0][%d:%d][%d:%d]" % (lon0, lon1, lat0, lat1)
    parts = [v + span for v in [VARIABLE] + EXTRA_VARIABLES]
    parts += ["lat[%d:%d]" % (lat0, lat1), "lon[%d:%d]" % (lon0, lon1), "time[0:0]"]

    path = "%s/%s/%s/%s" % (OPENDAP_BASE, stamp[:4], stamp[4:6], granule)
    return path + ".nc4?" + ",".join(parts)


def fetch_opendap(session, granule: str, dest: Path, cfg,
                  retries: int = 3) -> dict:
    """Fetch one granule as a server-side subset. Returns QC values."""
    import time as _time

    import numpy as np
    import xarray as xr

    url = opendap_url(granule, cfg)
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=180)
            if resp.status_code != 200:
                raise RuntimeError("HTTP %d" % resp.status_code)
            # Hyrax returns errors as HTML with a 200, so check the magic bytes.
            if resp.content[:4] not in (b"\x89HDF", b"CDF\x01"):
                raise RuntimeError(
                    "response is not netCDF (first bytes %r)" % resp.content[:16])
            break
        except Exception as exc:  # transient network or server error
            last_error = exc
            if attempt == retries:
                raise RuntimeError(
                    "OPeNDAP failed after %d attempts: %s" % (retries, exc))
            _time.sleep(2 ** attempt)
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError(str(last_error))

    transferred = len(resp.content)
    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    tmp_dest.write_bytes(resp.content)

    # Validate before accepting the file: a wrong index range would otherwise
    # produce a plausible-looking but silently shifted grid.
    c = cfg["region"]["crop"]
    try:
        with xr.open_dataset(tmp_dest, engine="h5netcdf") as ds:
            if VARIABLE not in ds:
                raise ValueError("subset has no '" + VARIABLE + "' variable")
            n_lat, n_lon = ds.sizes["lat"], ds.sizes["lon"]
            lat_min, lat_max = float(ds.lat.min()), float(ds.lat.max())
            lon_min, lon_max = float(ds.lon.min()), float(ds.lon.max())

            tol = IMERG_STEP / 2 + 1e-6
            if (abs(lat_min - c["south"]) > tol or abs(lat_max - c["north"]) > tol
                    or abs(lon_min - c["west"]) > tol
                    or abs(lon_max - c["east"]) > tol):
                raise ValueError(
                    "subset bounds %.2f..%.2f N, %.2f..%.2f E do not match the "
                    "crop box %.2f..%.2f N, %.2f..%.2f E"
                    % (lat_min, lat_max, lon_min, lon_max,
                       c["south"], c["north"], c["west"], c["east"]))

            values = ds[VARIABLE].values.astype("float64")
            finite = np.isfinite(values)
            qc = {
                "n_lat": int(n_lat),
                "n_lon": int(n_lon),
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
                "units": str(ds[VARIABLE].attrs.get("units", "")),
                "n_nan": int((~finite).sum()),
                "value_min": float(values[finite].min()) if finite.any() else None,
                "value_max": float(values[finite].max()) if finite.any() else None,
            }
    except Exception:
        tmp_dest.unlink(missing_ok=True)
        raise

    tmp_dest.replace(dest)
    qc["cropped_bytes"] = dest.stat().st_size
    qc["transferred_bytes"] = transferred
    qc["route"] = "opendap"
    return qc


# --- crop -------------------------------------------------------------------

def crop_file(src: Path, dest: Path, cfg) -> dict:
    """Subset one global IMERG granule to the crop box and write it out.

    Returns a dict of QC values. Raises on anything that would silently
    produce a wrong grid.
    """
    import numpy as np
    import xarray as xr

    c = cfg["region"]["crop"]
    with xr.open_dataset(src, engine="h5netcdf") as ds:
        if VARIABLE not in ds:
            raise ValueError(
                src.name + ": variable '" + VARIABLE + "' not present "
                "(have: " + str(list(ds.data_vars)) + ")"
            )

        lat_name = "lat" if "lat" in ds.coords else "latitude"
        lon_name = "lon" if "lon" in ds.coords else "longitude"

        # Slice in whichever direction each coordinate is stored.
        lat_vals = ds[lat_name].values
        lon_vals = ds[lon_name].values
        lat_slice = (slice(c["north"], c["south"]) if lat_vals[0] > lat_vals[-1]
                     else slice(c["south"], c["north"]))
        lon_slice = (slice(c["east"], c["west"]) if lon_vals[0] > lon_vals[-1]
                     else slice(c["west"], c["east"]))

        subset = ds.sel({lat_name: lat_slice, lon_name: lon_slice})

        n_lat = subset.sizes[lat_name]
        n_lon = subset.sizes[lon_name]
        if n_lat == 0 or n_lon == 0:
            raise ValueError(
                "%s: crop produced an empty grid (%d x %d). "
                "Source lat range %.2f..%.2f, lon range %.2f..%.2f."
                % (src.name, n_lat, n_lon, lat_vals.min(), lat_vals.max(),
                   lon_vals.min(), lon_vals.max())
            )

        values = subset[VARIABLE].values.astype("float64")
        finite = np.isfinite(values)
        qc = {
            "n_lat": int(n_lat),
            "n_lon": int(n_lon),
            "lat_min": float(subset[lat_name].min()),
            "lat_max": float(subset[lat_name].max()),
            "lon_min": float(subset[lon_name].min()),
            "lon_max": float(subset[lon_name].max()),
            "units": str(subset[VARIABLE].attrs.get("units", "")),
            "n_nan": int((~finite).sum()),
            "value_min": float(values[finite].min()) if finite.any() else None,
            "value_max": float(values[finite].max()) if finite.any() else None,
        }

        encoding = {VARIABLE: {"zlib": True, "complevel": 4}}
        # Write to a temp name first so a crash cannot leave a half file in raw/.
        tmp_dest = dest.with_suffix(dest.suffix + ".part")
        subset.to_netcdf(tmp_dest, engine="h5netcdf", encoding=encoding)

    tmp_dest.replace(dest)
    qc["cropped_bytes"] = dest.stat().st_size
    return qc


# --- main loop --------------------------------------------------------------

def process_month(cfg, month: SeasonMonth, logger, batch_size: int,
                  dry_run: bool, route: str):
    import earthaccess

    out_dir = cfg.get_path("raw_imerg")
    tmp_dir = Path(cfg["download"]["imerg_temp_dir"]) / str(month)

    results = search_month(cfg, month, logger)
    if not results:
        logger.error("%s: no granules returned - check credentials and product name",
                     month)
        return None

    manifest = Manifest(
        manifest_id="imerg_final_%d_%02d" % (month.year, month.month),
        source="NASA GES DISC via earthaccess",
        product=cfg["observations"]["product"],
        version=cfg["observations"]["version"],
        request={
            "temporal": [month.start.isoformat(), month.end.isoformat()],
            "bounding_box_wsen": list(crop_bbox_wsen(cfg)),
            "run": cfg["observations"]["run"],
            "variables": [VARIABLE] + EXTRA_VARIABLES,
            "route": route,
        },
    )
    manifest.set("expected_days", month.n_days)
    manifest.set("granules_found", len(results))

    if len(results) != month.n_days:
        manifest.add_issue(
            "granule_count_mismatch",
            "%d granules for a %d-day month" % (len(results), month.n_days),
        )
        logger.warning("%s: granule count %d != calendar days %d",
                       month, len(results), month.n_days)

    if dry_run:
        logger.info("%s: dry run, nothing downloaded", month)
        for r in results[:3]:
            logger.info("  sample granule: %s", granule_name(r))
        return manifest

    # Skip granules already cropped - data/raw is append-only.
    todo = []
    for r in results:
        name = granule_name(r)
        if (out_dir / name).exists():
            logger.debug("%s: already present, skipping", name)
        else:
            todo.append(r)
    logger.info("%s: %d to download, %d already present",
                month, len(todo), len(results) - len(todo))

    t_start = time.time()
    downloaded_bytes = 0

    if route == "opendap":
        session = earthaccess.get_requests_https_session()
        for n, r in enumerate(todo, 1):
            name = granule_name(r)
            dest = out_dir / name
            try:
                qc = fetch_opendap(session, name, dest, cfg)
            except Exception as exc:
                manifest.add_issue("opendap_failed", name + ": " + str(exc))
                logger.error("  %s: %s", name, exc)
                continue
            downloaded_bytes += qc["transferred_bytes"]
            manifest.add_file(dest, **qc)
            vmax = qc["value_max"] if qc["value_max"] is not None else float("nan")
            logger.info("  [%d/%d] %s -> %d x %d, %.1f kB over the wire, max %.1f mm",
                        n, len(todo), name, qc["n_lat"], qc["n_lon"],
                        qc["transferred_bytes"] / 1e3, vmax)
        return _finish_month(cfg, month, manifest, logger, out_dir,
                             t_start, downloaded_bytes)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        for i in range(0, len(todo), batch_size):
            batch = todo[i:i + batch_size]
            n_batches = (len(todo) + batch_size - 1) // batch_size
            logger.info("%s: batch %d/%d (%d granules)", month,
                        i // batch_size + 1, n_batches, len(batch))

            paths = earthaccess.download(batch, str(tmp_dir))

            for p in paths:
                src = Path(p)
                if not src.exists():
                    manifest.add_issue("download_missing",
                                       src.name + " not on disk after download")
                    logger.error("expected file missing after download: %s", src)
                    continue

                global_bytes = src.stat().st_size
                downloaded_bytes += global_bytes
                dest = out_dir / src.name
                try:
                    qc = crop_file(src, dest, cfg)
                except Exception as exc:
                    manifest.add_issue("crop_failed", src.name + ": " + str(exc))
                    logger.exception("crop failed for %s", src.name)
                    continue
                finally:
                    src.unlink(missing_ok=True)  # delete the global file

                qc["route"] = "download_crop"
                qc["transferred_bytes"] = global_bytes
                manifest.add_file(dest, global_bytes=global_bytes, **qc)
                vmax = qc["value_max"] if qc["value_max"] is not None else float("nan")
                logger.info("  %s -> %d x %d, %.1f kB (from %.1f MB), max %.1f mm",
                            src.name, qc["n_lat"], qc["n_lon"],
                            qc["cropped_bytes"] / 1e3, global_bytes / 1e6, vmax)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return _finish_month(cfg, month, manifest, logger, out_dir,
                         t_start, downloaded_bytes)


def describe_existing(path: Path, cfg) -> dict:
    """QC values for a file already on disk, without re-downloading it."""
    import numpy as np
    import xarray as xr

    with xr.open_dataset(path, engine="h5netcdf") as ds:
        values = ds[VARIABLE].values.astype("float64")
        finite = np.isfinite(values)
        return {
            "n_lat": int(ds.sizes["lat"]),
            "n_lon": int(ds.sizes["lon"]),
            "lat_min": float(ds.lat.min()),
            "lat_max": float(ds.lat.max()),
            "lon_min": float(ds.lon.min()),
            "lon_max": float(ds.lon.max()),
            "units": str(ds[VARIABLE].attrs.get("units", "")),
            "n_nan": int((~finite).sum()),
            "value_min": float(values[finite].min()) if finite.any() else None,
            "value_max": float(values[finite].max()) if finite.any() else None,
            "cropped_bytes": path.stat().st_size,
            "transferred_bytes": 0,      # already held locally
            "route": "pre_existing",
        }


def _finish_month(cfg, month: SeasonMonth, manifest: Manifest, logger,
                  out_dir: Path, t_start: float, downloaded_bytes: int):
    """Record timing and check the month against the calendar.

    A manifest describes every file the month has on disk, not just the ones
    this particular run fetched. Otherwise re-running a month that is already
    complete writes an empty manifest over a good one and the checksums
    required by CLAUDE.md section 5.5 are lost.
    """
    elapsed = time.time() - t_start
    manifest.set("download_seconds", round(elapsed, 1))
    manifest.set("bytes_transferred", downloaded_bytes)

    # Gap check against the calendar - report, never skip silently.
    pattern = "*3IMERG.%d%02d*" % (month.year, month.month)
    present = sorted(p.name for p in out_dir.glob(pattern))

    # Record files this run skipped because they were already downloaded.
    recorded = {f["file_name"] for f in manifest.data["files"]}
    for name in present:
        if name in recorded:
            continue
        path = out_dir / name
        try:
            manifest.add_file(path, **describe_existing(path, cfg))
        except Exception as exc:
            manifest.add_issue("existing_file_unreadable", name + ": " + str(exc))
            logger.error("  %s: already on disk but unreadable: %s", name, exc)
    n_reused = len(present) - len(recorded)
    if n_reused > 0:
        logger.info("%s: %d already-present files re-checksummed into the manifest",
                    month, n_reused)
    if len(present) != month.n_days:
        manifest.add_issue(
            "day_gap",
            "%d files on disk for a %d-day month" % (len(present), month.n_days),
        )
        logger.error("%s: GAP - %d of %d days present (%d missing)",
                     month, len(present), month.n_days,
                     month.n_days - len(present))
    else:
        logger.info("%s: complete, %d/%d days", month, len(present), month.n_days)

    logger.info("%s: %.2f MB over the wire, %.1f s, %d files written",
                month, downloaded_bytes / 1e6, elapsed, manifest.n_files)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, help="single year")
    ap.add_argument("--month", type=int, help="single month (needs --year)")
    ap.add_argument("--all", action="store_true",
                    help="every configured JJAS month")
    ap.add_argument("--route", default="opendap",
                    choices=["opendap", "download"],
                    help="opendap: server-side subset (~48 kB/day). "
                         "download: fetch the 34 MB global granule and crop "
                         "locally. Both give bitwise-identical values.")
    ap.add_argument("--batch-size", type=int, default=5,
                    help="granules per download batch (controls peak disk use)")
    ap.add_argument("--dry-run", action="store_true",
                    help="search only: report granule counts, download nothing")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("download_imerg")

    if args.all:
        months = list(season_months(cfg))
    elif args.year and args.month:
        months = [SeasonMonth(args.year, args.month)]
    elif args.year:
        months = list(season_months(cfg, [args.year]))
    else:
        ap.error("give --year [--month] or --all")

    logger.info("route: %s", args.route)
    logger.info("crop box (W,S,E,N): %s", crop_bbox_wsen(cfg))
    if args.route == "opendap":
        lat0, lat1, lon0, lon1 = grid_indices(cfg)
        logger.info("opendap index ranges: lat %d..%d, lon %d..%d",
                    lat0, lat1, lon0, lon1)
    logger.info("months to process: %s", ", ".join(str(m) for m in months))

    # Search needs authentication too, so this runs for dry runs as well.
    authenticate(logger)

    written, failed, total_issues = [], [], 0
    for month in months:
        try:
            manifest = process_month(cfg, month, logger, args.batch_size,
                                     args.dry_run, args.route)
        except Exception:
            logger.exception("%s: failed", month)
            failed.append(str(month))
            continue
        if manifest is None:
            failed.append(str(month))
            continue
        if not args.dry_run:
            path = manifest.write()
            total_issues += len(manifest.data["issues"])
            written.append(path.name)
            logger.info("%s: manifest %s", month, path.name)

    logger.info("done. manifests written: %d, months failed: %d, issues logged: %d",
                len(written), len(failed), total_issues)
    if failed:
        logger.error("failed months: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
