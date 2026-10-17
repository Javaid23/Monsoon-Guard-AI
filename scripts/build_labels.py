#!/usr/bin/env python
"""Build the label table: thresholds, signals and the two error targets.

Reads the raw IMERG and TIGGE archives, regrids observations onto the 0.5
degree forecast grid, computes per-cell thresholds from the training years
only, and writes one row per (issue date, lead day, cell).

Outputs
    data/interim/obs_on_forecast_grid.nc    observations, 0.5 degree
    data/processed/thresholds.nc            per-cell thresholds + provenance
    data/processed/labels.parquet           the label table
    logs/build_labels_<stamp>.log           what ran and what it found

Usage:
    python scripts/build_labels.py
    python scripts/build_labels.py --signal-fraction 0.30    # sensitivity run
    python scripts/build_labels.py --threshold-percentile 90
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import align, io_imerg, io_tigge, labels  # noqa: E402
from src.core.config import load_config  # noqa: E402
from src.core.manifest import setup_logging, utc_now_iso  # noqa: E402


def forecast_grid(cfg):
    """The 0.5 degree evaluation grid, latitudes descending."""
    r = cfg["region"]
    step = r["grid_resolution_deg"]
    lat = np.arange(r["north"], r["south"] - 1e-9, -step)
    lon = np.arange(r["west"], r["east"] + 1e-9, step)
    n_lat, n_lon = r["expected_shape"]
    if (len(lat), len(lon)) != (n_lat, n_lon):
        raise ValueError("built grid %s but config expects %s"
                         % ((len(lat), len(lon)), r["expected_shape"]))
    return lat, lon


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signal-fraction", type=float, default=None,
                    help="override the configured signal level (sensitivity runs)")
    ap.add_argument("--threshold-percentile", type=float, default=None,
                    help="override the configured event percentile")
    ap.add_argument("--group", default="precip")
    ap.add_argument("--type", default="pf")
    ap.add_argument("--suffix", default="",
                    help="suffix for output names, for sensitivity runs")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("build_labels")
    lat, lon = forecast_grid(cfg)

    # --- observations -------------------------------------------------------
    obs, obs_qc = io_imerg.load(cfg)
    for line in obs_qc.report().splitlines():
        logger.info("%s", line)
    if not obs_qc.complete:
        logger.error("IMERG is incomplete: %d missing days. Labels built from "
                     "an incomplete archive are not usable for evaluation.",
                     len(obs_qc.missing_dates))

    obs_fc = align.regrid_to_forecast(obs, lat, lon, cfg)
    logger.info("observations regridded to %s", tuple(obs_fc.shape))

    interim = cfg.get_path("interim") / ("obs_on_forecast_grid%s.nc" % args.suffix)
    obs_fc.to_netcdf(interim, engine="h5netcdf")
    logger.info("wrote %s", interim)

    # --- thresholds (training years only) -----------------------------------
    th = labels.compute_thresholds(obs_fc, cfg,
                                   percentile=args.threshold_percentile)
    for line in th.report().splitlines():
        logger.info("%s", line)

    forbidden = (set(cfg["split"]["calibration_years"])
                 | set(cfg["split"]["test_years"]))
    leaked = forbidden & set(th.years_used)
    if leaked:
        logger.error("LEAKAGE: thresholds used non-training years %s", sorted(leaked))
        return 1
    logger.info("leakage check passed: thresholds use only %s", th.years_used)

    low = int((th.values.values < 1.0).sum())
    if low:
        logger.warning("%d of %d cells have a threshold below 1 mm; in those "
                       "cells 'extreme' means little more than measurable rain "
                       "(see docs/decisions.md)", low, th.values.values.size)

    th_path = cfg.get_path("processed") / ("thresholds%s.nc" % args.suffix)
    th.values.to_netcdf(th_path, engine="h5netcdf")
    logger.info("wrote %s", th_path)

    # --- forecasts ----------------------------------------------------------
    fc, qcs = io_tigge.load(cfg, group=args.group, fc_type=args.type)
    logger.info("loaded %d TIGGE file(s), %d issue dates, %d members",
                len(qcs), fc.sizes["time"], fc.sizes["number"])
    total_neg = sum(q.n_negative_clipped for q in qcs)
    total_val = sum(q.n_values for q in qcs)
    worst = min([q.most_negative for q in qcs], default=0.0)
    logger.info("negative differences clipped: %d of %d (%.4f%%), most "
                "negative %.4f mm", total_neg, total_val,
                100 * total_neg / total_val if total_val else 0.0, worst)

    months = sorted({str(t)[:7] for t in fc["time"].values})
    logger.info("forecast months present: %s", ", ".join(months))
    expected_months = 20
    if len(months) < expected_months:
        logger.warning("only %d of %d JJAS months are downloaded; the label "
                       "table covers those months only", len(months),
                       expected_months)

    # --- labels -------------------------------------------------------------
    table = labels.build_label_table(fc, obs_fc, th, cfg,
                                     signal_fraction=args.signal_fraction)
    dropped = table.attrs["missing_observations"]
    if dropped:
        logger.warning("%d (issue_date, lead_day) pairs dropped for want of a "
                       "verifying observation; first few: %s",
                       len(dropped), dropped[:5])

    rates = labels.base_rates(table)
    for line in labels.format_base_rates(rates).splitlines():
        logger.info("%s", line)
    for lead, group in table.groupby("lead_day"):
        r = labels.base_rates(group)
        logger.info("  lead day %d: signal %.1f%%, events %.2f%%, "
                    "false alarm %.1f%%, miss %.2f%%", lead,
                    100 * r["signal_rate"], 100 * r["event_rate"],
                    100 * r["false_alarm_rate"], 100 * r["miss_rate"])

    out = cfg.get_path("processed") / ("labels%s.parquet" % args.suffix)
    table.to_parquet(out, index=False)
    logger.info("wrote %s (%s rows)", out, format(len(table), ","))

    summary = {
        "built_utc": utc_now_iso(),
        "rows": len(table),
        "threshold_percentile": th.percentile,
        "threshold_years": th.years_used,
        "threshold_days": th.n_days_used,
        "signal_fraction": table.attrs["signal_fraction"],
        "forecast_months": months,
        "negatives_clipped": total_neg,
        "most_negative_mm": worst,
        "cells_below_1mm_threshold": low,
        "dropped_pairs": len(dropped),
        "base_rates": rates,
    }
    sidecar = out.with_suffix(".summary.json")
    sidecar.write_text(json.dumps(summary, indent=2, sort_keys=True),
                       encoding="utf-8")
    logger.info("wrote %s", sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
