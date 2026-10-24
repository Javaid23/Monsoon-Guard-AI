#!/usr/bin/env python
"""Provisional baseline run: features + model ladder on whatever TIGGE
precipitation months are currently on disk.

THIS IS NOT THE CLAUDE.md SECTION 8 EVALUATION. It exists to validate the
pipeline end to end and get honest provisional numbers while the archive is
still downloading, using leave-one-month-out cross-validation over whatever
TRAIN-split months are on disk.

It refuses to run once any calibration or test month has been downloaded, so
it cannot be mistaken for, or quietly substituted for, the frozen protocol.

Re-running this script after more months land will automatically use the
larger set; nothing here is hard-coded to a fixed number of months.

Usage:
    python scripts/provisional_baseline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import align, features, io_imerg, io_tigge, labels, models  # noqa: E402
from src.core.config import load_config, split_months  # noqa: E402
from src.core.manifest import setup_logging  # noqa: E402


def find_omi_path(cfg) -> Path | None:
    candidates = sorted(cfg.get_path("raw_mjo").glob("omi_*.txt"))
    return candidates[-1] if candidates else None


def main() -> int:
    cfg = load_config()
    logger = setup_logging("provisional_baseline")
    logger.warning("PROVISIONAL RUN - not the CLAUDE.md section 8 evaluation. "
                   "Leave-one-month-out CV over train-split months only. "
                   "Refuses to run once calibration or test data exists.")

    # --- data actually on disk right now ------------------------------------
    import numpy as np
    lat = np.arange(cfg["region"]["north"], cfg["region"]["south"] - 1e-9,
                    -cfg["region"]["grid_resolution_deg"])
    lon = np.arange(cfg["region"]["west"], cfg["region"]["east"] + 1e-9,
                    cfg["region"]["grid_resolution_deg"])

    obs, obs_qc = io_imerg.load(cfg)
    logger.info("IMERG: %d files, complete=%s", obs_qc.n_files, obs_qc.complete)
    obs_fc = align.regrid_to_forecast(obs, lat, lon, cfg)

    pf_months = io_tigge.list_files(cfg, "precip", "pf")
    logger.info("TIGGE perturbed months on disk: %d -> %s",
                len(pf_months), [p.name for p in pf_months])
    if not pf_months:
        logger.error("no TIGGE precipitation files on disk yet - nothing to run")
        return 1

    forecast, qcs = io_tigge.load(cfg, "precip", "pf")
    months_present = sorted({str(t)[:7] for t in forecast["time"].values})
    train_m = set(split_months(cfg, "train"))
    forbidden = set(split_months(cfg, "calibration")) | set(split_months(cfg, "test"))
    present = set(months_present)
    if present & forbidden:
        logger.error("calibration/test month data is present (%s) - this "
                     "script must not be used once it exists; use the frozen "
                     "evaluation protocol instead", sorted(present & forbidden))
        return 1
    if not present & train_m:
        logger.error("none of the downloaded months (%s) are in the train "
                     "split (%s) - nothing to do", months_present,
                     sorted(train_m))
        return 1
    logger.info("months used (all within the train split): %s",
                sorted(present & train_m))

    # --- thresholds, labels, features ---------------------------------------
    th = labels.compute_thresholds(obs_fc, cfg,
                                   months=sorted(present & train_m))
    logger.info("thresholds computed from months %s only (%d days)",
                th.months_used, th.n_days_used)

    table = labels.build_label_table(forecast, obs_fc, th, cfg)
    rates = labels.base_rates(table)
    for line in labels.format_base_rates(rates).splitlines():
        logger.info("%s", line)

    omi_path = find_omi_path(cfg)
    if omi_path:
        logger.info("MJO features from %s", omi_path.name)
    else:
        logger.warning("no OMI file found - running without MJO features")

    feat = features.build_feature_table(forecast, th.values, omi_path=omi_path, cfg=cfg)

    joined = table.merge(
        feat.drop(columns=["day_of_year"], errors="ignore"),
        on=["issue_date", "lead_day", "latitude", "longitude"], how="inner")
    logger.info("joined label+feature table: %s rows (labels had %s, "
                "features had %s)", format(len(joined), ","),
                format(len(table), ","), format(len(feat), ","))
    if len(joined) != len(table):
        logger.warning("join dropped %d rows - check label/feature row "
                       "construction for a mismatch", len(table) - len(joined))

    # --- model ladder, leave-one-month-out ----------------------------------
    cols = models.feature_columns(joined, include_omi=omi_path is not None)
    logger.info("feature columns (%d): %s", len(cols), cols)
    signal_level = float(cfg["labels"]["signal_fraction"])

    results = []
    for target in ("false_alarm", "miss"):
        data = models.prepare_subset(joined, target, cols)
        logger.info("%s subset: %s rows, %s positive, %d dropped for NaN features",
                    target, format(len(data.y), ","), int(data.y.sum()),
                    data.n_dropped_nan)
        if len(set(data.month)) < 2:
            logger.warning("%s: fewer than 2 distinct months present - "
                           "leave-one-month-out is not meaningful yet, skipping",
                           target)
            continue
        for model_name in ("reference", "raw_reliability", "logistic"):
            try:
                result = models.leave_one_month_out(data, model_name, target,
                                                    signal_level)
            except Exception as exc:
                logger.error("%s / %s failed: %s", target, model_name, exc)
                continue
            results.append(result)
            logger.info("%s", result.metrics.report("%s / %s" % (target, model_name)))

    if not results:
        logger.error("no model could be evaluated - check subset sizes above")
        return 1

    out_dir = cfg.get_path("processed")
    import json
    summary = [
        {"target": r.target, "model": r.model_name, "months": r.months,
         "n": r.metrics.n, "n_positive": r.metrics.n_positive,
         "brier": r.metrics.brier,
         "brier_skill_vs_reference": r.metrics.brier_skill_vs_reference,
         "roc_auc": r.metrics.roc_auc, "pr_auc": r.metrics.pr_auc,
         "n_dropped_nan": r.n_dropped_nan}
        for r in results
    ]
    path = out_dir / "provisional_baseline_results.json"
    path.write_text(json.dumps({
        "warning": "PROVISIONAL - not the frozen CLAUDE.md section 8 evaluation",
        "months_used": months_present,
        "results": summary,
    }, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
