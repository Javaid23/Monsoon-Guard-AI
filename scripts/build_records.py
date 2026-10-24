#!/usr/bin/env python
"""Build public forecast records and commit them to the mock registry.

Uses real predictions from the frozen evaluation's test split. Each record is
committed with a simulated clock set to the forecast issue time, because the
registry refuses any commit at or after its window opens - the same rule the
contract enforces. The simulated clock is recorded in the output so nobody
mistakes these for live commits.

Usage:
    python scripts/build_records.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit import record as rec_mod  # noqa: E402
from src.audit.registry_client import MockRegistry  # noqa: E402
from src.audit.verify import verify_record  # noqa: E402
from src.core import align, calibrate, features, io_imerg, io_tigge  # noqa: E402
from src.core import labels as labels_mod  # noqa: E402
from src.core import models as models_mod  # noqa: E402
from src.core.config import load_config, split_months  # noqa: E402
from src.core.manifest import setup_logging  # noqa: E402


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild the cached joined table from raw data")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many records to build (region-level, one per "
                         "issue date and lead day)")
    args = ap.parse_args()

    cfg = load_config()
    logger = setup_logging("build_records")
    commit_sha = git_commit()

    r = cfg["region"]
    step = r["grid_resolution_deg"]
    lat = np.arange(r["north"], r["south"] - 1e-9, -step)
    lon = np.arange(r["west"], r["east"] + 1e-9, step)

    # The IMERG load and regrid take several minutes, so the joined table is
    # cached. Delete the cache (or pass --rebuild) after changing any upstream
    # step, or you will silently build records from stale features.
    cache = cfg.get_path("interim") / "joined_table.parquet"
    omi = sorted(cfg.get_path("raw_mjo").glob("omi_*.txt"))

    if cache.exists() and not args.rebuild:
        data = pd.read_parquet(cache)
        logger.info("loaded cached joined table: %s rows from %s",
                    format(len(data), ","), cache.name)
    else:
        obs, _ = io_imerg.load(cfg)
        obs_fc = align.regrid_to_forecast(obs, lat, lon, cfg)
        forecast, _ = io_tigge.load(cfg, "precip", "pf")

        th = labels_mod.compute_thresholds(obs_fc, cfg)
        table = labels_mod.build_label_table(forecast, obs_fc, th, cfg)
        feat = features.build_feature_table(
            forecast, th.values, omi_path=omi[-1] if omi else None, cfg=cfg)
        data = table.merge(feat.drop(columns=["day_of_year"], errors="ignore"),
                           on=["issue_date", "lead_day", "latitude", "longitude"],
                           how="inner")
        data["month"] = pd.to_datetime(data["issue_date"]).dt.strftime("%Y-%m")
        data.to_parquet(cache, index=False)
        logger.info("built and cached joined table: %s rows", format(len(data), ","))

    cols = models_mod.feature_columns(data, include_omi=bool(omi))
    train_m, cal_m, test_m = (split_months(cfg, k)
                              for k in ("train", "calibration", "test"))

    # Train the false-alarm model and its calibrator exactly as the frozen
    # evaluation does, then produce records for the test split.
    sub = data[data["false_alarm"].notna()].dropna(subset=cols)
    tr = sub[sub["month"].isin(train_m)]
    ca = sub[sub["month"].isin(cal_m)]
    scaler, model = models_mod.fit_logistic(tr[cols].to_numpy(),
                                            tr["false_alarm"].to_numpy())
    cal_scores = model.predict_proba(scaler.transform(ca[cols].to_numpy()))[:, 1]
    calibrator = calibrate.fit_calibrator(cal_scores,
                                          ca["false_alarm"].to_numpy(), "isotonic")

    # Region-level summary per (issue date, lead day): CLAUDE.md section 10.2
    # says commit a summary per region and keep the cell table in the record
    # file. Lead day 1 is excluded - section 10.3: its window has already
    # opened by the time the 00 UTC run is available.
    te = sub[sub["month"].isin(test_m) & (sub["lead_day"] >= 2)]
    logger.info("test rows for records: %d (test months %s, lead day >= 2)",
                len(te), test_m)
    if te.empty:
        logger.error("no test rows with lead day >= 2. months present: %s, "
                     "lead days present: %s", sorted(sub["month"].unique()),
                     sorted(sub["lead_day"].unique()))
        return 1
    scores = model.predict_proba(scaler.transform(te[cols].to_numpy()))[:, 1]
    te = te.assign(p_wrong=calibrator.apply(scores))

    grouped = (te.groupby(["issue_date", "lead_day"])
                 .agg(signal_fraction=("ens_frac_over_threshold", "mean"),
                      p_wrong=("p_wrong", "mean"),
                      n_cells=("p_wrong", "size"),
                      observed_mm=("obs_mm", "mean"),
                      n_events=("y_event", "sum"),
                      n_wrong=("false_alarm", "sum"))
                 .reset_index()
                 .sort_values(["issue_date", "lead_day"])
                 .head(args.limit))

    records_dir = cfg.get_path("records")
    registry = MockRegistry(records_dir / "mock_registry.json")
    manifest_ids = sorted(p.stem for p in cfg.get_path("manifests").glob("*.json"))

    built, verified = 0, 0
    for _, row in grouped.iterrows():
        issue = datetime.strptime(row["issue_date"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        lead = int(row["lead_day"])
        window_start = issue + timedelta(days=lead - 1)
        window_end = window_start + timedelta(days=1)

        fid = rec_mod.forecast_id(r["id"], row["issue_date"], lead)
        p_wrong = float(row["p_wrong"])
        label = str(calibrate.trust_label(np.array([p_wrong]), cfg)[0])

        fr = rec_mod.ForecastRecord(
            forecast_id=fid,
            region_id=r["id"],
            issue_time_utc=issue.strftime("%Y-%m-%dT%H:%M:%SZ"),
            window_start_utc=window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            window_end_utc=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            lead_hours=lead * 24,
            signal_bp=rec_mod.to_basis_points(float(row["signal_fraction"])),
            p_wrong_bp=rec_mod.to_basis_points(p_wrong),
            trust_label=label,
            threshold_rule="p95_jjas_train_months_per_cell",
            threshold_manifest="thresholds_v1",
            signal_rule="member_fraction_ge_%dbp"
                        % rec_mod.to_basis_points(cfg["labels"]["signal_fraction"]),
            trust_band_rule="bands_v1",
            model_version=cfg["project"]["model_version"],
            input_data_manifest="manifests_%d_files" % len(manifest_ids),
            code_commit=commit_sha,
        )

        # The registry clock is set to a few hours after the 00 UTC run, which
        # is when the forecast would really have been available, and is before
        # the day-2/day-3 window opens.
        registry._now = lambda i=issue: i + timedelta(hours=6)
        try:
            registry.commit(fid, fr.hash(),
                            fr.window_start_utc, fr.window_end_utc)
        except Exception as exc:
            logger.error("%s: commit refused: %s", fid, exc)
            continue

        outcome = rec_mod.OutcomeRecord(
            forecast_id=fid, forecast_hash=fr.hash(),
            observation_product=cfg["observations"]["product"],
            observation_version=cfg["observations"]["version"],
            observation_run=cfg["observations"]["run"],
            window_start_utc=fr.window_start_utc,
            window_end_utc=fr.window_end_utc,
            observed_value_hundredths_mm=rec_mod.to_hundredths_mm(
                float(row["observed_mm"])),
            event_occurred=bool(row["n_events"] > 0),
            decision_was_wrong=bool(row["n_wrong"] > row["n_cells"] / 2),
            retrieved_utc=(window_end + timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"))

        registry._now = lambda w=window_end: w + timedelta(days=1)
        registry.record_outcome(fid, outcome.hash())

        (records_dir / ("%s.json" % fid)).write_text(
            json.dumps(fr.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        (records_dir / ("%s.outcome.json" % fid)).write_text(
            json.dumps(outcome.as_dict(), indent=2, sort_keys=True),
            encoding="utf-8")

        result = verify_record(fr.as_dict(), registry.get(fid), outcome.as_dict())
        built += 1
        if result.ok:
            verified += 1
        else:
            logger.error("%s did not verify: %s", fid, result.status.value)
        logger.info("%s  %-6s p_wrong=%5.1f%%  %s", fid, label,
                    100 * p_wrong, result.status.value)

    logger.info("built %d records, %d verified, registry at %s",
                built, verified, registry.path)
    logger.warning("These records use a SIMULATED clock set to the historical "
                   "issue time. They demonstrate the mechanism on archived "
                   "forecasts; they are not live commitments.")
    return 0 if built and built == verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
