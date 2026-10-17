"""Tests for the IMERG and TIGGE loaders.

The accumulation arithmetic is tested on synthetic arrays where the right
answer is known by hand. The structural checks are tested against the real
GRIB when it is present, and skipped otherwise so the suite still runs on a
clean checkout.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import load_config  # noqa: E402
from src.core.io_tigge import accumulations_to_windows  # noqa: E402
import src.core.io_imerg as io_imerg  # noqa: E402
import src.core.io_tigge as io_tigge  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# --- accumulation arithmetic ------------------------------------------------

def test_windows_are_successive_differences():
    """day1 = step24, day2 = step48 - step24, day3 = step72 - step48."""
    acc = np.array([[[[[2.0]], [[5.0]], [[9.0]]]]])      # (1,1,3,1,1)
    windows, n_neg, worst = accumulations_to_windows(acc, step_axis=2)
    assert windows.ravel().tolist() == [2.0, 3.0, 4.0]
    assert n_neg == 0
    assert worst == 0.0


def test_window_totals_sum_back_to_the_final_accumulation():
    rng = np.random.default_rng(0)
    acc = np.cumsum(rng.random((2, 4, 3, 5, 6)), axis=2)
    windows, n_neg, _ = accumulations_to_windows(acc, step_axis=2)
    assert n_neg == 0
    assert np.allclose(windows.sum(axis=2), acc[:, :, -1])


def test_negative_differences_are_clipped_and_counted():
    """GRIB packing can make an accumulation decrease very slightly."""
    acc = np.array([[[[[10.0]], [[9.9375]], [[12.0]]]]])
    windows, n_neg, worst = accumulations_to_windows(acc, step_axis=2)
    assert n_neg == 1
    assert worst == pytest.approx(-0.0625)
    assert windows.min() == 0.0
    assert windows.ravel().tolist() == [10.0, 0.0, pytest.approx(2.0625)]


def test_no_negatives_reports_zero_not_a_false_minimum():
    acc = np.array([[[[[1.0]], [[2.0]], [[3.0]]]]])
    _, n_neg, worst = accumulations_to_windows(acc, step_axis=2)
    assert (n_neg, worst) == (0, 0.0)


def test_windows_are_never_negative_after_clipping():
    rng = np.random.default_rng(1)
    acc = np.cumsum(rng.random((3, 3, 3, 4, 4)), axis=2)
    # Make step 1 fall below step 0, the way GRIB rounding can.
    acc[0, 0, 1] = acc[0, 0, 0] - 0.0625
    windows, n_neg, worst = accumulations_to_windows(acc, step_axis=2)
    assert n_neg == acc[0, 0, 0].size
    assert worst == pytest.approx(-0.0625)
    assert (windows >= 0).all()


def test_input_is_not_modified_in_place():
    acc = np.array([[[[[5.0]], [[4.0]], [[6.0]]]]])
    before = acc.copy()
    accumulations_to_windows(acc, step_axis=2)
    assert np.array_equal(acc, before)


# --- IMERG file naming ------------------------------------------------------

def test_granule_date_parsing():
    name = "3B-DAY.MS.MRG.3IMERG.20220815-S000000-E235959.V07B.nc4"
    assert io_imerg.granule_date(name) == date(2022, 8, 15)


def test_granule_date_rejects_a_non_granule():
    with pytest.raises(ValueError, match="not an IMERG granule"):
        io_imerg.granule_date("something_else.nc4")


def test_expected_dates_cover_the_season(cfg):
    days = io_imerg.expected_dates(cfg, [2022])
    assert len(days) == 122
    assert days[0] == date(2022, 6, 1)
    assert days[-1] == date(2022, 9, 30)


# --- real data --------------------------------------------------------------

IMERG_DIR = ROOT / "data" / "raw" / "imerg_final"
GRIB = ROOT / "data" / "raw" / "tigge_ncep" / "precip_pf_2022_08.grib"
has_imerg = IMERG_DIR.exists() and any(IMERG_DIR.glob("*3IMERG*.nc4"))


@pytest.mark.skipif(not has_imerg, reason="IMERG not downloaded")
def test_imerg_loads_a_complete_season(cfg):
    data, qc = io_imerg.load(cfg, years=[2022])
    assert qc.n_files == 122
    assert qc.missing_dates == []
    assert qc.complete
    assert qc.units == "mm/day"
    assert qc.n_nan == 0
    assert data.dims == ("time", "lat", "lon")
    assert data.sizes["lat"] == 76 and data.sizes["lon"] == 66


@pytest.mark.skipif(not has_imerg, reason="IMERG not downloaded")
def test_imerg_time_axis_is_sorted_and_unique(cfg):
    data, _ = io_imerg.load(cfg, years=[2022])
    times = data.time.values
    assert len(set(times.tolist())) == len(times)
    assert (np.diff(times).astype("int64") > 0).all()


@pytest.mark.skipif(not GRIB.exists(), reason="TIGGE GRIB not downloaded")
def test_tigge_file_matches_the_frozen_design(cfg):
    da, qc = io_tigge.load_file(GRIB, cfg)
    assert qc.n_members == 30
    assert qc.steps_h == [24, 48, 72]
    assert qc.shape == (15, 13)
    assert qc.lat_descending
    assert qc.grid_deg == pytest.approx(0.5)
    assert qc.units == "kg m**-2"
    assert qc.step_type == "accum"
    assert da.dims == ("number", "time", "lead_day", "latitude", "longitude")
    assert list(da.lead_day.values) == [1, 2, 3]
    assert float(da.min()) >= 0.0


@pytest.mark.skipif(not GRIB.exists(), reason="TIGGE GRIB not downloaded")
def test_tigge_latitudes_descend_37_to_30(cfg):
    da, _ = io_tigge.load_file(GRIB, cfg)
    lat = da.latitude.values
    assert lat[0] == pytest.approx(37.0)
    assert lat[-1] == pytest.approx(30.0)
    assert (np.diff(lat) < 0).all()


@pytest.mark.skipif(not GRIB.exists(), reason="TIGGE GRIB not downloaded")
def test_tigge_negative_clipping_is_bounded_by_packing_precision(cfg):
    """Rounding artefacts only: nothing should exceed the 1/16 mm packing step."""
    _, qc = io_tigge.load_file(GRIB, cfg)
    assert qc.most_negative >= -0.07, (
        "a large negative difference means a decoding error, not rounding")
    assert qc.negative_share < 0.05


@pytest.mark.skipif(not GRIB.exists(), reason="TIGGE GRIB not downloaded")
def test_claude_md_phase1_ensemble_area_mean(cfg):
    """CLAUDE.md section 13: ensemble area mean range 7.6 to 18.8 mm for the
    2022-08-15 00 UTC run, lead day 1."""
    da, _ = io_tigge.load_file(GRIB, cfg)
    day1 = da.sel(lead_day=1).sel(time="2022-08-15T00:00:00")
    per_member = day1.mean(dim=["latitude", "longitude"]).values
    assert float(per_member.min()) == pytest.approx(7.6, abs=0.1)
    assert float(per_member.max()) == pytest.approx(18.8, abs=0.1)
