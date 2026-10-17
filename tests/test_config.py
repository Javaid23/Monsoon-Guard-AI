"""Tests for configuration and the season calendar.

These cover the invariants CLAUDE.md section 13 requires: non-overlapping
splits, the expected grid shape and orientation, and a crop box that actually
contains the evaluation region.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import (  # noqa: E402
    SeasonMonth,
    crop_bbox_wsen,
    expected_season_days,
    load_config,
    region_area_nwse,
    season_months,
)


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_config_loads(cfg):
    assert cfg["project"]["name"] == "monsoonguard"
    assert isinstance(cfg["project"]["seed"], int)


def test_region_matches_claude_md(cfg):
    r = cfg["region"]
    assert (r["south"], r["north"]) == (30.0, 37.0)
    assert (r["west"], r["east"]) == (69.0, 75.0)
    assert r["grid_resolution_deg"] == 0.5


def test_grid_shape_is_15_by_13(cfg):
    """15 latitudes (37->30 N) by 13 longitudes (69->75 E) = 195 cells."""
    n_lat, n_lon = cfg["region"]["expected_shape"]
    assert (n_lat, n_lon) == (15, 13)
    assert n_lat * n_lon == 195


def test_crop_box_is_a_quarter_degree_wider(cfg):
    """0.5 deg cell averaging needs half a cell of margin on every side."""
    r, c = cfg["region"], cfg["region"]["crop"]
    assert c["north"] - r["north"] == pytest.approx(0.25)
    assert r["south"] - c["south"] == pytest.approx(0.25)
    assert c["east"] - r["east"] == pytest.approx(0.25)
    assert r["west"] - c["west"] == pytest.approx(0.25)


def test_bbox_orderings_are_not_mixed_up(cfg):
    """CMR wants west,south,east,north; MARS wants N/W/S/E. Easy to swap."""
    west, south, east, north = crop_bbox_wsen(cfg)
    assert west < east and south < north
    assert (west, south, east, north) == (68.75, 29.75, 75.25, 37.25)
    assert region_area_nwse(cfg) == "37.0/69.0/30.0/75.0"


def test_splits_do_not_overlap(cfg):
    train = set(cfg["split"]["train_years"])
    cal = set(cfg["split"]["calibration_years"])
    test = set(cfg["split"]["test_years"])
    assert not train & cal
    assert not train & test
    assert not cal & test


def test_test_year_is_last(cfg):
    """The chronological split must not train on the future."""
    assert max(cfg["split"]["train_years"]) < min(cfg["split"]["calibration_years"])
    assert max(cfg["split"]["calibration_years"]) < min(cfg["split"]["test_years"])


def test_every_season_year_is_assigned_to_a_split(cfg):
    assigned = (set(cfg["split"]["train_years"])
                | set(cfg["split"]["calibration_years"])
                | set(cfg["split"]["test_years"]))
    assert assigned == set(cfg["season"]["years"])


def test_trust_bands_ordered(cfg):
    b = cfg["trust_bands"]
    assert 0 < b["high_max"] < b["medium_max"] < 1
    assert b["high_max"] == 0.20
    assert b["medium_max"] == 0.50


def test_season_is_jjas(cfg):
    assert cfg["season"]["months"] == [6, 7, 8, 9]


def test_season_month_boundaries():
    june = SeasonMonth(2022, 6)
    assert june.start == date(2022, 6, 1)
    assert june.end == date(2022, 6, 30)
    assert june.n_days == 30

    july = SeasonMonth(2022, 7)
    assert july.end == date(2022, 7, 31)
    assert july.n_days == 31


def test_season_day_count_is_610(cfg):
    """JJAS is 122 days; five seasons is 610. CLAUDE.md section 3 says 'about 610'."""
    assert expected_season_days(cfg, [2022]) == 122
    assert expected_season_days(cfg) == 610


def test_season_months_are_in_order(cfg):
    months = list(season_months(cfg))
    assert len(months) == 20
    assert str(months[0]) == "2021-06"
    assert str(months[-1]) == "2025-09"
    keys = [(m.year, m.month) for m in months]
    assert keys == sorted(keys)


def test_forecast_settings_match_sample_file(cfg):
    """NCEP, 00 UTC, three lead windows, 30 perturbed members.

    The centre is spelled "ncep" on ECDS; the MARS code "kwbc" in CLAUDE.md
    section 3 belonged to the decommissioned apps.ecmwf.int service.
    """
    fc = cfg["forecast"]
    assert fc["dataset"] == "tigge-forecasts"
    assert fc["origin"] == "ncep"
    assert fc["issue_time_utc"] == "00:00"
    assert fc["steps_h"] == [24, 48, 72]
    assert fc["expected_grid_deg"] == 0.5
    assert fc["members"]["perturbed"] == 30
    assert fc["forecast_types"]["perturbed"] == "perturbed_forecast"
    assert fc["forecast_types"]["control"] == "control_forecast"


def test_label_rules_match_frozen_defaults(cfg):
    assert cfg["labels"]["threshold_percentile"] == 95
    assert cfg["labels"]["signal_fraction"] == 0.20
    assert 0.20 in cfg["labels"]["sensitivity"]["signal_fractions"]
