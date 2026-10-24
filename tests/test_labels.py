"""Tests for thresholds, signals and the two conditional error targets.

The leakage tests here are the ones CLAUDE.md section 13 requires: thresholds
must come from the training years alone, and no feature may use information
from after the forecast issue time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import load_config  # noqa: E402
from src.core.labels import (  # noqa: E402
    base_rates,
    build_label_table,
    compute_thresholds,
    error_targets,
    forecast_signal,
    member_fraction_over,
    observed_events,
)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# --- synthetic data ---------------------------------------------------------

LAT = np.arange(37.0, 29.99, -0.5)      # 15, descending
LON = np.arange(69.0, 75.01, 0.5)       # 13


def _obs(years, seed=0, scale=1.0):
    """Daily JJAS rainfall for the given years, on the forecast grid."""
    rng = np.random.default_rng(seed)
    times = []
    for y in years:
        times.extend(pd.date_range("%d-06-01" % y, "%d-09-30" % y, freq="D"))
    times = pd.DatetimeIndex(times)
    data = rng.gamma(0.6, 6.0, size=(len(times), len(LAT), len(LON))) * scale
    return xr.DataArray(data, dims=("time", "latitude", "longitude"),
                        coords={"time": times, "latitude": LAT, "longitude": LON})


def _forecast(issue_dates, n_members=30, seed=1, scale=1.0):
    rng = np.random.default_rng(seed)
    data = rng.gamma(0.6, 6.0,
                     size=(n_members, len(issue_dates), 3, len(LAT), len(LON))) * scale
    return xr.DataArray(
        data, dims=("number", "time", "lead_day", "latitude", "longitude"),
        coords={"number": np.arange(1, n_members + 1), "time": issue_dates,
                "lead_day": [1, 2, 3], "latitude": LAT, "longitude": LON})


# --- thresholds -------------------------------------------------------------

def test_thresholds_use_only_training_months(cfg):
    """Calibration and test months must not influence the threshold."""
    from src.core.config import split_months
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg)
    assert th.months_used == sorted(split_months(cfg, "train"))
    for m in split_months(cfg, "calibration") + split_months(cfg, "test"):
        assert m not in th.months_used


def test_threshold_is_unchanged_by_altering_test_years(cfg):
    """Strongest form of the leakage check: perturb 2025 wildly and the
    threshold must not move at all."""
    obs = _obs([2021, 2022, 2023, 2024, 2025])
    baseline = compute_thresholds(obs, cfg).values.values

    tampered = obs.copy(deep=True)
    years = pd.to_datetime(tampered["time"].values).year
    tampered[np.flatnonzero(years == 2025)] *= 1000.0
    after = compute_thresholds(tampered, cfg).values.values

    assert np.array_equal(baseline, after)


def test_threshold_day_count_matches_the_training_calendar(cfg):
    """The active train split is 2021 JJAS: 30+31+31+30 = 122 days."""
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg)
    assert th.n_days_used == 122
    assert th.years_used == [2021]


def test_threshold_is_the_requested_percentile(cfg):
    """A known distribution must give the known quantile."""
    times = pd.date_range("2021-06-01", "2021-09-30", freq="D")
    ramp = np.arange(len(times), dtype="float64")
    data = np.repeat(ramp[:, None, None], len(LAT), 1).repeat(len(LON), 2)
    obs = xr.DataArray(data, dims=("time", "latitude", "longitude"),
                       coords={"time": times, "latitude": LAT, "longitude": LON})
    th = compute_thresholds(obs, cfg, years=[2021], percentile=95)
    assert float(th.values.values[0, 0]) == pytest.approx(np.quantile(ramp, 0.95))


def test_thresholds_vary_by_cell(cfg):
    """Per-cell, not one number for the region (a fixed 50 mm would be wrong)."""
    rng = np.random.default_rng(3)
    times = pd.date_range("2021-06-01", "2021-09-30", freq="D")
    scale = np.linspace(1.0, 10.0, len(LAT))[None, :, None]
    data = rng.gamma(2.0, 3.0, (len(times), len(LAT), len(LON))) * scale
    obs = xr.DataArray(data, dims=("time", "latitude", "longitude"),
                       coords={"time": times, "latitude": LAT, "longitude": LON})
    th = compute_thresholds(obs, cfg, years=[2021])
    per_lat = th.values.values.mean(axis=1)
    assert per_lat[-1] > per_lat[0] * 3


def test_thresholds_reject_the_observation_grid(cfg):
    """Thresholds belong on the 0.5 deg forecast grid, never the 0.1 deg one."""
    times = pd.date_range("2021-06-01", "2021-06-30", freq="D")
    obs = xr.DataArray(np.zeros((len(times), 4, 4)),
                       dims=("time", "lat", "lon"),
                       coords={"time": times, "lat": np.arange(4),
                               "lon": np.arange(4)})
    with pytest.raises(ValueError, match="forecast grid"):
        compute_thresholds(obs, cfg, years=[2021])


def test_thresholds_fail_loudly_on_an_absent_year(cfg):
    obs = _obs([2021])
    with pytest.raises(ValueError, match="no observations"):
        compute_thresholds(obs, cfg, years=[2030])


# --- events and signals -----------------------------------------------------

def test_event_is_strict_exceedance(cfg):
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    events = observed_events(obs, th)
    assert set(np.unique(events.values)) <= {0, 1}
    # p95 over the same period should fire on about 5 percent of days.
    assert float(events.mean()) == pytest.approx(0.05, abs=0.01)


def test_member_fraction_is_a_fraction_not_a_count(cfg):
    """Ensemble size can change between years; counts would not compare."""
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    dates = pd.date_range("2022-08-01", periods=5, freq="D")

    f30 = member_fraction_over(_forecast(dates, 30, seed=7), th)
    f20 = member_fraction_over(_forecast(dates, 20, seed=7), th)
    for f in (f30, f20):
        assert float(f.min()) >= 0.0 and float(f.max()) <= 1.0
    assert f30.shape == f20.shape


def test_signal_threshold_is_inclusive(cfg):
    """'at least 20 percent' includes exactly 20 percent."""
    frac = xr.DataArray(np.array([0.0, 0.19, 0.20, 0.21, 1.0]), dims="x")
    sig = forecast_signal(frac, cfg)
    assert sig.values.tolist() == [0, 0, 1, 1, 1]


def test_signal_level_is_configurable_for_sensitivity_runs(cfg):
    frac = xr.DataArray(np.array([0.05, 0.15, 0.35, 0.60]), dims="x")
    assert forecast_signal(frac, cfg, 0.10).values.tolist() == [0, 1, 1, 1]
    assert forecast_signal(frac, cfg, 0.50).values.tolist() == [0, 0, 0, 1]


# --- conditional error targets ---------------------------------------------

def test_error_targets_are_conditional_not_zero_filled():
    """Outside its own subset each target is NaN. A zero would invent a
    negative example and bias model A or B."""
    signal = np.array([1, 1, 0, 0])
    y_event = np.array([1, 0, 1, 0])
    fa, miss = error_targets(signal, y_event)

    assert fa[0] == 0.0 and fa[1] == 1.0          # signal=1: wrong when no event
    assert np.isnan(fa[2]) and np.isnan(fa[3])
    assert miss[2] == 1.0 and miss[3] == 0.0      # signal=0: wrong when event
    assert np.isnan(miss[0]) and np.isnan(miss[1])


def test_every_sample_belongs_to_exactly_one_subset():
    rng = np.random.default_rng(5)
    signal = rng.integers(0, 2, 500)
    y_event = rng.integers(0, 2, 500)
    fa, miss = error_targets(signal, y_event)
    defined = (~np.isnan(fa)).astype(int) + (~np.isnan(miss)).astype(int)
    assert (defined == 1).all()


def test_a_correct_forecast_is_never_an_error():
    signal = np.array([1, 0])
    y_event = np.array([1, 0])          # hit, and correct rejection
    fa, miss = error_targets(signal, y_event)
    assert fa[0] == 0.0
    assert miss[1] == 0.0


# --- label table ------------------------------------------------------------

def test_label_table_has_one_row_per_issue_lead_cell(cfg):
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    dates = pd.date_range("2022-08-01", periods=4, freq="D")
    table = build_label_table(_forecast(dates), obs, th, cfg)
    assert len(table) == 4 * 3 * 15 * 13
    assert table.groupby(["issue_date", "lead_day"]).size().unique().tolist() == [195]


def test_label_table_valid_date_follows_the_lead_day(cfg):
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    dates = pd.DatetimeIndex(["2022-08-15"])
    table = build_label_table(_forecast(dates), obs, th, cfg)
    got = (table.groupby("lead_day")["valid_date"].first().to_dict())
    assert got == {1: "2022-08-15", 2: "2022-08-16", 3: "2022-08-17"}


def test_label_table_drops_unverifiable_dates_and_records_them(cfg):
    """An issue date whose verifying observation is absent must be dropped
    and counted, never silently filled."""
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    # 2022-09-30 lead 2 and 3 verify on 1 and 2 October, which JJAS lacks.
    dates = pd.DatetimeIndex(["2022-09-30"])
    table = build_label_table(_forecast(dates), obs, th, cfg)
    assert sorted(table["lead_day"].unique().tolist()) == [1]
    assert len(table.attrs["missing_observations"]) == 2


def test_label_table_targets_are_mutually_exclusive(cfg):
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    dates = pd.date_range("2022-08-01", periods=3, freq="D")
    table = build_label_table(_forecast(dates), obs, th, cfg)
    both = table["false_alarm"].notna() & table["miss"].notna()
    neither = table["false_alarm"].isna() & table["miss"].isna()
    assert not both.any()
    assert not neither.any()


def test_base_rates_are_internally_consistent(cfg):
    obs = _obs([2021, 2022, 2023])
    th = compute_thresholds(obs, cfg, years=[2021, 2022, 2023])
    dates = pd.date_range("2022-08-01", periods=6, freq="D")
    table = build_label_table(_forecast(dates), obs, th, cfg)
    r = base_rates(table)
    assert r["n_signal"] + r["n_quiet"] == r["n_samples"]
    assert 0.0 <= r["event_rate"] <= 1.0
    assert r["n_false_alarm"] <= r["n_signal"]
    assert r["n_miss"] <= r["n_quiet"]
