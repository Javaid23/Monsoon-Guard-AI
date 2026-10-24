#!/usr/bin/env python
"""The frozen evaluation (CLAUDE.md section 8). Run once, after every choice
is final.

Pipeline:
  train split       fit thresholds and both conditional error models
  calibration split fit the probability calibrator and nothing else
  test split        predict, calibrate, band, and score - touched once

Everything the run used is written to reports/ alongside the numbers, so a
reader can tell exactly which months, which features and which cutoffs
produced them.

Usage:
    python scripts/run_evaluation.py
    python scripts/run_evaluation.py --model logistic --calibrator isotonic
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import align, calibrate, evaluate, features, io_imerg, io_tigge  # noqa: E402
from src.core import labels as labels_mod  # noqa: E402
from src.core import models as models_mod  # noqa: E402
from src.core.config import load_config, split_months  # noqa: E402
from src.core.manifest import setup_logging, utc_now_iso  # noqa: E402


def month_of(table: pd.DataFrame) -> np.ndarray:
    return pd.to_datetime(table["issue_date"]).dt.strftime("%Y-%m").values


def fit_model(name, X, y):
    if name == "logistic":
        return models_mod.fit_logistic(X, y)
    if name == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler().fit(X)
        model = RandomForestClassifier(
            n_estimators=300, min_samples_leaf=20, class_weight="balanced",
            random_state=0, n_jobs=-1)
        model.fit(scaler.transform(X), y)
        return scaler, model
    raise ValueError("unknown model: " + name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="logistic",
                    choices=["logistic", "random_forest"])
    ap.add_argument("--calibrator", default="isotonic",
                    choices=["isotonic", "platt"])
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("run_evaluation")
    seed = int(cfg["project"]["seed"])
    np.random.seed(seed)

    train_m = split_months(cfg, "train")
    cal_m = split_months(cfg, "calibration")
    test_m = split_months(cfg, "test")
    logger.info("FROZEN SPLIT  train=%s  calibration=%s  test=%s",
                train_m, cal_m, test_m)
    logger.info("model=%s  calibrator=%s  seed=%d", args.model, args.calibrator, seed)

    # --- data ---------------------------------------------------------------
    r = cfg["region"]
    step = r["grid_resolution_deg"]
    lat = np.arange(r["north"], r["south"] - 1e-9, -step)
    lon = np.arange(r["west"], r["east"] + 1e-9, step)

    obs, obs_qc = io_imerg.load(cfg)
    logger.info("IMERG: %d files, complete=%s", obs_qc.n_files, obs_qc.complete)
    obs_fc = align.regrid_to_forecast(obs, lat, lon, cfg)

    forecast, qcs = io_tigge.load(cfg, "precip", "pf")
    have = sorted({str(t)[:7] for t in forecast["time"].values})
    logger.info("TIGGE months available: %s", have)

    missing = [m for m in train_m + cal_m + test_m if m not in have]
    if missing:
        logger.error("the frozen split needs months that are not downloaded: %s",
                     missing)
        return 1

    # --- thresholds: TRAIN ONLY ---------------------------------------------
    th = labels_mod.compute_thresholds(obs_fc, cfg)
    for line in th.report().splitlines():
        logger.info("%s", line)
    leaked = set(th.months_used) & set(cal_m + test_m)
    if leaked:
        logger.error("LEAKAGE: thresholds used calibration/test months %s",
                     sorted(leaked))
        return 1
    logger.info("leakage check passed: thresholds from train months only")

    # --- labels and features -------------------------------------------------
    table = labels_mod.build_label_table(forecast, obs_fc, th, cfg)
    omi = sorted(cfg.get_path("raw_mjo").glob("omi_*.txt"))
    omi_path = omi[-1] if omi else None
    feat = features.build_feature_table(forecast, th.values, omi_path=omi_path,
                                        cfg=cfg)
    data = table.merge(feat.drop(columns=["day_of_year"], errors="ignore"),
                       on=["issue_date", "lead_day", "latitude", "longitude"],
                       how="inner")
    data["month"] = month_of(data)
    logger.info("joined table: %s rows", format(len(data), ","))

    cols = models_mod.feature_columns(data, include_omi=omi_path is not None)
    logger.info("features (%d): %s", len(cols), cols)

    results = {"run_utc": utc_now_iso(), "model": args.model,
               "calibrator": args.calibrator, "seed": seed,
               "split": {"train": train_m, "calibration": cal_m, "test": test_m},
               "features": cols, "targets": {}}

    # --- forecast verification on the test split (section 8.2) --------------
    test_rows = data[data["month"].isin(test_m)]
    levels = cfg["labels"]["sensitivity"]["signal_fractions"]
    verif = evaluate.contingency_by_signal_level(
        test_rows["ens_frac_over_threshold"].to_numpy(),
        test_rows["y_event"].to_numpy(), levels)
    results["forecast_verification_test"] = verif
    for level, d in verif.items():
        logger.info("verification @ signal>=%-5s POD=%.3f FAR=%.3f CSI=%.3f "
                    "miss=%.3f bias=%.3f", level, d["probability_of_detection"],
                    d["false_alarm_ratio"], d["critical_success_index"],
                    d["miss_rate"], d["frequency_bias"])

    by_lead = {}
    for lead, g in test_rows.groupby("lead_day"):
        ct = evaluate.contingency(g["signal"].to_numpy(), g["y_event"].to_numpy())
        by_lead[int(lead)] = ct.as_dict()
        logger.info("%s", ct.report("test lead day %d" % lead))
    results["forecast_verification_by_lead"] = by_lead

    # --- the two conditional error models -----------------------------------
    for target in ("false_alarm", "miss"):
        logger.info("=== %s model ===", target)
        sub = data[data[target].notna()].dropna(subset=cols)
        tr = sub[sub["month"].isin(train_m)]
        ca = sub[sub["month"].isin(cal_m)]
        te = sub[sub["month"].isin(test_m)]
        logger.info("rows  train=%s  calibration=%s  test=%s",
                    format(len(tr), ","), format(len(ca), ","), format(len(te), ","))
        logger.info("positives  train=%d  calibration=%d  test=%d",
                    int(tr[target].sum()), int(ca[target].sum()), int(te[target].sum()))

        if len(tr) == 0 or len(te) == 0:
            logger.error("%s: empty train or test subset, skipping", target)
            continue
        if tr[target].nunique() < 2:
            logger.error("%s: training subset has only one class, skipping", target)
            continue

        scaler, model = fit_model(args.model, tr[cols].to_numpy(),
                                  tr[target].to_numpy())

        def score(df):
            return model.predict_proba(scaler.transform(df[cols].to_numpy()))[:, 1]

        cal_scores = score(ca) if len(ca) else np.array([])
        calibrator = (calibrate.fit_calibrator(cal_scores, ca[target].to_numpy(),
                                               args.calibrator)
                      if len(ca) else
                      calibrate.Calibrator("identity", None, 0, 0))
        logger.info("calibrator: %s fitted on %d calibration rows (%d positive)",
                    calibrator.kind, calibrator.n_fit, calibrator.n_positive_fit)

        # Test split - touched once, here.
        y_test = te[target].to_numpy()
        p_raw = score(te)
        p_wrong = calibrator.apply(p_raw)

        base_rate = float(tr[target].mean())
        p_ref = np.full(len(y_test), base_rate)

        m_raw = models_mod.evaluate(y_test, p_raw, p_ref)
        m_cal = models_mod.evaluate(y_test, p_wrong, p_ref)
        m_ref = models_mod.evaluate(y_test, p_ref, p_ref)
        for label, m in (("reference", m_ref), ("uncalibrated", m_raw),
                         ("calibrated", m_cal)):
            logger.info("%s", m.report("%s / %s" % (target, label)))

        rel = calibrate.reliability_curve(p_wrong, y_test)
        logger.info("expected calibration error: %.4f",
                    rel["expected_calibration_error"])

        bands = calibrate.band_error_rates(p_wrong, y_test, cfg)
        for band, d in bands.items():
            logger.info("  %-6s n=%-7s %5.1f%% of samples  error rate %6.2f%%  "
                        "captures %5.1f%% of all errors", band,
                        format(d["n"], ","), 100 * d["share_of_samples"],
                        100 * d["error_rate"] if d["n"] else float("nan"),
                        100 * d["share_of_all_errors"])

        weeks = evaluate.calendar_week(te["valid_date"])
        ci = evaluate.block_bootstrap_ci((y_test == 1).astype(float), weeks,
                                         seed=seed)
        logger.info("test error rate %.4f  95%% CI [%.4f, %.4f] "
                    "(block bootstrap, %d calendar weeks)",
                    ci["point_estimate"], ci["ci_low"], ci["ci_high"],
                    ci["n_blocks"])

        results["targets"][target] = {
            "n_train": len(tr), "n_calibration": len(ca), "n_test": len(te),
            "n_positive_train": int(tr[target].sum()),
            "n_positive_test": int(te[target].sum()),
            "train_base_rate": base_rate,
            "calibrator_kind": calibrator.kind,
            "metrics": {
                "reference": m_ref.__dict__,
                "uncalibrated": m_raw.__dict__,
                "calibrated": m_cal.__dict__,
            },
            "reliability": rel,
            "trust_bands": bands,
            "test_error_rate_ci": ci,
        }

    out = Path(cfg.get_path("figures")).parent / "evaluation_results.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True, default=str),
                   encoding="utf-8")
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
