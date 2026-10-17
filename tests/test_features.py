"""Tests for ensemble, neighbourhood, context and OMI features.

The OMI tests are the temporal-correctness checks CLAUDE.md requires: a
feature may never use information from after the forecast issue date.
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
from src.core.features import (  # noqa: E402
    build_feature_table,
    context_features,
    ensemble_features,
    neighbourhood_features,
    omi_features,
)

LAT = np.arange(37.0, 29.99, -0.5)       # 15
LON = np.arange(69.0, 75.01, 0.5)        # 13


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def _members(seed=0, n_members=30, n_time=4, n_lead=3):
    rng = np.random.default_rng(seed)
    data = rng.gamma(0.6, 6.0, size=(n_members, n_time, n_lead, len(LAT), len(LON)))
    return xr.DataArray(
        data, dims=("number", "time", "lead_day", "latitude", "longitude"),
        coords={"number": np.arange(1, n_members + 1),
               "time": pd.date_range("2022-08-01", periods=n_time, freq="D"),
               "lead_day": list(range(1, n_lead + 1)),
               "latitude": LAT, "longitude": LON})


def _threshold(value=10.0):
    return xr.DataArray(np.full((len(LAT), len(LON)), value),
                        dims=("latitude", "longitude"),
                        coords={"latitude": LAT, "longitude": LON})


# --- ensemble features -------------------------------------------------------

def test_ensemble_mean_matches_a_known_distribution():
    members = xr.DataArray(np.arange(1, 31, dtype="float64"), dims="number")
    th = xr.DataArray(0.0)
    feats = ensemble_features(members, th)
    assert feats["ens_mean"].item() == pytest.approx(15.5)
    assert feats["ens_max"].item() == 30.0


def test_fraction_over_threshold_is_a_fraction():
    members = xr.DataArray(np.array([5.0] * 6 + [15.0] * 24), dims="number")
    th = xr.DataArray(10.0)
    feats = ensemble_features(members, th)
    assert feats["ens_frac_over_threshold"].item() == pytest.approx(24 / 30)


def test_half_threshold_fraction_is_at_least_full_threshold_fraction():
    m = _members()
    th = _threshold()
    feats = ensemble_features(m, th)
    assert (feats["ens_frac_over_half_threshold"].values
            >= feats["ens_frac_over_threshold"].values - 1e-9).all()


def test_spread_normalisation_handles_a_near_zero_mean():
    """A dry cell (near-zero ensemble mean) must not produce inf or NaN."""
    members = xr.DataArray(np.zeros(30) + 1e-9, dims="number")
    th = xr.DataArray(1.0)
    feats = ensemble_features(members, th)
    assert np.isfinite(feats["ens_std_norm"].item())


def test_quantiles_are_ordered():
    m = _members(seed=3)
    th = _threshold()
    feats = ensemble_features(m, th)
    q25, q50, q75, q90 = (feats["ens_q%02d" % q].values for q in (25, 50, 75, 90))
    assert (q25 <= q50 + 1e-9).all()
    assert (q50 <= q75 + 1e-9).all()
    assert (q75 <= q90 + 1e-9).all()


# --- neighbourhood features --------------------------------------------------

def test_neighbourhood_mean_of_a_constant_field_is_the_constant():
    field = xr.DataArray(np.full((2, len(LAT), len(LON)), 3.5),
                         dims=("time", "latitude", "longitude"),
                         coords={"time": [0, 1], "latitude": LAT, "longitude": LON})
    out = neighbourhood_features(field)
    assert np.allclose(out["neigh_mean"].values, 3.5)
    assert np.allclose(out["neigh_max"].values, 3.5)


def test_neighbourhood_requires_trailing_lat_lon_dims():
    field = xr.DataArray(np.zeros((len(LAT), len(LON), 2)),
                         dims=("latitude", "longitude", "time"))
    with pytest.raises(AssertionError, match="last two dimensions"):
        neighbourhood_features(field)


def test_neighbourhood_corner_uses_a_2x2_window_not_wraparound():
    """A spike in the opposite corner must not leak into the near corner."""
    data = np.zeros((len(LAT), len(LON)))
    data[0, 0] = 9.0        # top-left corner
    data[-1, -1] = 100.0    # bottom-right corner, far away
    field = xr.DataArray(data, dims=("latitude", "longitude"),
                        coords={"latitude": LAT, "longitude": LON})
    out = neighbourhood_features(field)
    # Top-left neighbourhood is a 2x2 window: {9, 0, 0, 0} -> mean 2.25.
    assert out["neigh_mean"].values[0, 0] == pytest.approx(9.0 / 4)
    assert out["neigh_max"].values[0, 0] == pytest.approx(9.0)


def test_neighbourhood_mean_differs_from_centre_value_near_a_spike():
    data = np.zeros((len(LAT), len(LON)))
    data[5, 5] = 90.0
    field = xr.DataArray(data, dims=("latitude", "longitude"),
                        coords={"latitude": LAT, "longitude": LON})
    out = neighbourhood_features(field)
    assert out["neigh_mean"].values[5, 5] == pytest.approx(90.0 / 9)
    assert out["neigh_mean"].values[5, 6] == pytest.approx(90.0 / 9)   # adjacent cell


# --- context features --------------------------------------------------------

def test_context_features_row_count_and_lead_day_range():
    dates = pd.date_range("2022-08-01", periods=3, freq="D")
    table = context_features(dates, [1, 2, 3], LAT, LON)
    assert len(table) == 3 * 3 * len(LAT) * len(LON)
    assert set(table["lead_day"].unique()) == {1, 2, 3}


def test_day_of_year_is_correct():
    dates = pd.date_range("2022-01-01", periods=1, freq="D")
    table = context_features(dates, [1], LAT[:1], LON[:1])
    assert table["day_of_year"].iloc[0] == 1


# --- OMI (monsoon) features --------------------------------------------------

@pytest.fixture
def omi_file(tmp_path):
    p = tmp_path / "omi.txt"
    p.write_text(
        "2022 7 30 0.10 -0.20 0.60\n"
        "2022 7 31 0.15 -0.25 0.65\n"
        "2022 8 1  0.20 -0.30 0.70\n"
        "2022 8 2  0.25 -0.35 0.75\n",
        encoding="utf-8",
    )
    return p


def test_omi_uses_the_value_on_or_before_the_issue_date(omi_file):
    """Temporal correctness: never a date after the issue date."""
    dates = pd.to_datetime(["2022-08-01", "2022-08-02"])
    out = omi_features(dates, omi_file)
    assert out.loc[0, "omi_date_used"] == "2022-08-01"
    assert out.loc[1, "omi_date_used"] == "2022-08-02"
    assert out.loc[0, "omi_pc1"] == pytest.approx(0.20)


def test_omi_falls_back_to_the_most_recent_earlier_date(omi_file):
    """A date with no exact OMI entry must use the last value before it,
    never a later one."""
    dates = pd.to_datetime(["2022-08-05"])    # no entry for this date
    out = omi_features(dates, omi_file)
    assert out.loc[0, "omi_date_used"] == "2022-08-02"
    assert out.loc[0, "omi_pc1"] == pytest.approx(0.25)


def test_omi_is_nan_before_the_record_begins(omi_file):
    dates = pd.to_datetime(["2000-01-01"])
    out = omi_features(dates, omi_file)
    assert np.isnan(out.loc[0, "omi_pc1"])
    assert out.loc[0, "omi_date_used"] is None


def test_omi_phase_is_zero_at_low_amplitude(omi_file):
    """An amplitude below 1.0 means no coherent MJO signal."""
    dates = pd.to_datetime(["2022-08-01"])   # amplitude 0.70 in the fixture
    out = omi_features(dates, omi_file)
    assert out.loc[0, "omi_phase"] == 0


def test_omi_phase_is_one_of_eight_sectors_at_high_amplitude(tmp_path):
    p = tmp_path / "omi.txt"
    # Eight points around the unit circle, amplitude comfortably above 1.
    p.write_text("\n".join(
        "2022 1 %d %f %f 2.0" % (i + 1, np.cos(a), np.sin(a))
        for i, a in enumerate(np.linspace(0, 2 * np.pi, 8, endpoint=False))
    ), encoding="utf-8")
    dates = pd.date_range("2022-01-01", periods=8, freq="D")
    out = omi_features(dates, p)
    assert set(out["omi_phase"]) <= set(range(1, 9))
    assert len(set(out["omi_phase"])) > 1       # different angles, different phases


# --- full feature table -------------------------------------------------------

def test_feature_table_row_count_matches_label_table(cfg):
    from src.core.labels import build_label_table, compute_thresholds

    members = _members(n_time=3)
    obs_times = pd.date_range("2022-08-01", periods=5, freq="D")
    obs = xr.DataArray(
        np.random.default_rng(1).gamma(0.6, 6.0, (5, len(LAT), len(LON))),
        dims=("time", "latitude", "longitude"),
        coords={"time": obs_times, "latitude": LAT, "longitude": LON})
    th = compute_thresholds(obs, cfg, years=[2022])

    labels = build_label_table(members, obs, th, cfg)
    features = build_feature_table(members, th.values, cfg=cfg)
    assert len(features) == len(labels)


def test_feature_table_has_no_duplicate_rows(cfg):
    members = _members(n_time=2)
    th = _threshold()
    table = build_feature_table(members, th, cfg=cfg)
    key = table[["issue_date", "lead_day", "latitude", "longitude"]]
    assert not key.duplicated().any()


def test_feature_table_joins_omi_when_a_path_is_given(cfg, omi_file):
    members = _members(n_time=2, n_lead=1)
    # Members built on 2022-08-01, 08-02; omi_file covers up to 2022-08-02.
    th = _threshold()
    table = build_feature_table(members, th, omi_path=omi_file, cfg=cfg)
    assert "omi_pc1" in table.columns
    assert not table["omi_pc1"].isna().all()
