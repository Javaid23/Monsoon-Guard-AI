"""Tests for time-window logic and IMERG-to-TIGGE regridding.

These cover the CLAUDE.md section 13 requirements on time, units and grid
orientation. Everything here is synthetic except the acceptance check at the
end, which runs against the real sample day when the data is present.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.align import (  # noqa: E402
    Window,
    build_weights,
    effective_cell_counts,
    regrid_to_forecast,
    windows_for,
)
from src.core.config import load_config  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# --- time windows -----------------------------------------------------------

def test_lead_day_1_covers_the_issue_day(cfg):
    """Day 1 of the 00 UTC run on D covers D 00 UTC to D+1 00 UTC, and is
    verified by the IMERG file for D (CLAUDE.md section 6.1)."""
    w = Window(date(2022, 8, 15), 1)
    assert w.window_start_utc.isoformat() == "2022-08-15T00:00:00"
    assert w.window_end_utc.isoformat() == "2022-08-16T00:00:00"
    assert w.valid_date == date(2022, 8, 15)


def test_lead_days_2_and_3_shift_by_one_day_each(cfg):
    assert Window(date(2022, 8, 15), 2).valid_date == date(2022, 8, 16)
    assert Window(date(2022, 8, 15), 3).valid_date == date(2022, 8, 17)


def test_windows_are_exactly_24_hours(cfg):
    for lead in (1, 2, 3):
        w = Window(date(2022, 8, 15), lead)
        assert (w.window_end_utc - w.window_start_utc).total_seconds() == 86400


def test_windows_do_not_overlap_and_are_contiguous(cfg):
    ws = windows_for(date(2022, 8, 15), cfg)
    assert len(ws) == 3
    for a, b in zip(ws, ws[1:]):
        assert a.window_end_utc == b.window_start_utc


def test_window_crosses_month_and_year_boundaries():
    assert Window(date(2022, 8, 31), 2).valid_date == date(2022, 9, 1)
    assert Window(date(2022, 12, 31), 3).valid_date == date(2023, 1, 2)


def test_window_columns_are_explicit(cfg):
    cols = Window(date(2022, 8, 15), 2).as_columns()
    assert set(cols) == {"issue_date", "lead_day", "window_start_utc",
                         "window_end_utc", "valid_date"}
    assert cols["window_start_utc"].endswith("Z")
    assert cols["valid_date"] == "2022-08-16"


# --- weights ----------------------------------------------------------------

def test_weights_sum_to_one_per_target(cfg):
    src = np.arange(29.75, 37.26, 0.1)
    tgt = np.arange(30.0, 37.01, 0.5)
    w = build_weights(src, tgt, 0.25)
    assert np.allclose(w.sum(axis=1), 1.0)


def test_edge_cells_get_half_weight(cfg):
    """A source centre exactly on a box edge is shared with the neighbour."""
    src = np.array([29.75, 29.85, 29.95, 30.05, 30.15, 30.25])
    w = build_weights(src, np.array([30.0]), 0.25)
    raw = w[0] * w[0].sum() / w[0].sum()   # normalised row
    # Four interior cells at weight 1, two edge cells at 0.5 -> 5.0 total.
    unnormalised = np.array([0.5, 1, 1, 1, 1, 0.5])
    assert np.allclose(w[0], unnormalised / unnormalised.sum())
    assert raw[0] == pytest.approx(raw[-1])        # symmetric


def test_effective_cells_per_box_is_25(cfg):
    """A 0.5 deg box holds exactly 5x5 cells of 0.1 deg."""
    lat = np.arange(29.75, 37.26, 0.1)
    lon = np.arange(68.75, 75.26, 0.1)
    counts = effective_cell_counts(lat, lon,
                                   np.arange(37.0, 29.99, -0.5),
                                   np.arange(69.0, 75.01, 0.5), cfg)
    assert np.allclose(counts, 25.0)


def test_weights_reject_a_crop_box_that_is_too_small():
    src = np.array([30.0, 30.1])
    with pytest.raises(ValueError, match="crop box is too small"):
        build_weights(src, np.array([36.0]), 0.25)


# --- regridding -------------------------------------------------------------

def _fake_obs(value_fn):
    lat = np.arange(29.75, 37.2501, 0.1)
    lon = np.arange(68.75, 75.2501, 0.1)
    data = value_fn(lat[:, None], lon[None, :])
    return xr.DataArray(data, dims=("lat", "lon"),
                        coords={"lat": lat, "lon": lon})


def test_constant_field_survives_regridding(cfg):
    """Averaging a uniform field must return the same constant."""
    obs = _fake_obs(lambda la, lo: np.full(np.broadcast(la, lo).shape, 4.2))
    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    assert out.shape == (15, 13)
    assert np.allclose(out.values, 4.2)


def test_regrid_preserves_grid_orientation(cfg):
    """Output latitudes must stay descending 37 -> 30."""
    obs = _fake_obs(lambda la, lo: np.broadcast_to(la, np.broadcast(la, lo).shape))
    target_lat = np.arange(37.0, 29.99, -0.5)
    out = regrid_to_forecast(obs, target_lat, np.arange(69.0, 75.01, 0.5), cfg)
    assert out.latitude.values[0] == pytest.approx(37.0)
    assert out.latitude.values[-1] == pytest.approx(30.0)
    # A field equal to latitude must average back to each box centre.
    assert np.allclose(out.values[:, 0], target_lat)


def test_regrid_is_mass_sensible_on_a_linear_field(cfg):
    """A linear ramp in longitude averages to the box-centre value."""
    obs = _fake_obs(lambda la, lo: np.broadcast_to(lo, np.broadcast(la, lo).shape))
    target_lon = np.arange(69.0, 75.01, 0.5)
    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5), target_lon, cfg)
    assert np.allclose(out.values[0, :], target_lon)


def test_regrid_handles_a_leading_time_dimension(cfg):
    lat = np.arange(29.75, 37.2501, 0.1)
    lon = np.arange(68.75, 75.2501, 0.1)
    data = np.stack([np.full((len(lat), len(lon)), v) for v in (1.0, 2.0, 3.0)])
    obs = xr.DataArray(data, dims=("time", "lat", "lon"),
                       coords={"time": [1, 2, 3], "lat": lat, "lon": lon})
    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    assert out.shape == (3, 15, 13)
    for i, v in enumerate((1.0, 2.0, 3.0)):
        assert np.allclose(out.values[i], v)


# --- acceptance check against real data -------------------------------------

SAMPLE = "3B-DAY.MS.MRG.3IMERG.20220815-S000000-E235959.V07B.nc4"


@pytest.mark.skipif(not (ROOT / "data" / "raw" / "imerg_final" / SAMPLE).exists(),
                    reason="IMERG sample day not downloaded")
def test_claude_md_phase1_imerg_area_mean(cfg):
    """CLAUDE.md section 13: IMERG 0.5 deg area mean about 7.2 mm on 2022-08-15."""
    from src.core.io_imerg import load_day

    obs = load_day(date(2022, 8, 15), cfg)
    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    assert out.shape == (15, 13)
    assert not np.isnan(out.values).any()
    assert float(out.mean()) == pytest.approx(7.2, abs=0.05)


# --- NaN handling (regression: a single missing cell once destroyed the grid)

def test_one_missing_observation_does_not_poison_the_whole_grid(cfg):
    """Zero weights do not exclude a NaN, because 0 * NaN is NaN. A plain
    contraction therefore turned every one of the 195 output cells into NaN
    when a single input cell was missing. The averaging must be NaN-aware."""
    lat = np.arange(29.75, 37.2501, 0.1)
    lon = np.arange(68.75, 75.2501, 0.1)
    data = np.ones((len(lat), len(lon)))
    data[0, 0] = np.nan
    obs = xr.DataArray(data, dims=("lat", "lon"),
                       coords={"lat": lat, "lon": lon})

    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    assert not np.isnan(out.values).any()
    # The box containing the gap still averages its remaining cells to 1.0.
    assert np.allclose(out.values, 1.0)


def test_a_fully_missing_box_reports_nan_rather_than_inventing_a_value(cfg):
    lat = np.arange(29.75, 37.2501, 0.1)
    lon = np.arange(68.75, 75.2501, 0.1)
    data = np.ones((len(lat), len(lon)))
    data[0:6, 0:6] = np.nan          # covers one 0.5 deg box entirely
    obs = xr.DataArray(data, dims=("lat", "lon"),
                       coords={"lat": lat, "lon": lon})

    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    assert int(np.isnan(out.values).sum()) == 1


def test_partial_coverage_averages_only_the_present_cells(cfg):
    """A box half full of 2.0 and half missing must average to 2.0, not 1.0:
    missing cells are absent, not zero."""
    lat = np.arange(29.75, 37.2501, 0.1)
    lon = np.arange(68.75, 75.2501, 0.1)
    data = np.full((len(lat), len(lon)), 2.0)
    data[0:3, :] = np.nan
    obs = xr.DataArray(data, dims=("lat", "lon"),
                       coords={"lat": lat, "lon": lon})

    out = regrid_to_forecast(obs, np.arange(37.0, 29.99, -0.5),
                             np.arange(69.0, 75.01, 0.5), cfg)
    finite = out.values[np.isfinite(out.values)]
    assert np.allclose(finite, 2.0)
