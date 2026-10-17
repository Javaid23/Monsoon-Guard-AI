"""Tests for the request builders in the download scripts.

A wrong bounding box or a wrong step list produces data that looks fine and is
scientifically useless, so the request parameters are checked here rather than
discovered after a multi-hour download.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import SeasonMonth, load_config  # noqa: E402


def _load_script(name: str):
    """Import a file from scripts/ as a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def tigge():
    return _load_script("download_tigge")


# --- TIGGE request (ECDS) ---------------------------------------------------

def test_precip_request_matches_the_frozen_design(cfg, tigge):
    groups = tigge.param_groups(cfg)
    req = tigge.build_request(cfg, SeasonMonth(2022, 8), groups["precip"],
                              "perturbed_forecast")

    assert req["origin"] == "ncep"
    assert req["level_type"] == "single_level"
    assert req["variable"] == ["total_precipitation"]
    assert req["forecast_type"] == "perturbed_forecast"
    assert req["year"] == "2022"
    assert req["month"] == "08"
    assert req["time"] == "00:00"
    assert req["leadtime_hour"] == ["24", "48", "72"]
    assert req["data_format"] == "grib"
    # ECDS geographic extent is [north, west, south, east].
    assert req["area"] == [37.0, 69.0, 30.0, 75.0]


def test_request_covers_every_day_of_the_month(cfg, tigge):
    groups = tigge.param_groups(cfg)
    for month, n in [(SeasonMonth(2022, 6), 30), (SeasonMonth(2022, 7), 31),
                     (SeasonMonth(2022, 8), 31), (SeasonMonth(2022, 9), 30)]:
        req = tigge.build_request(cfg, month, groups["precip"],
                                  "perturbed_forecast")
        assert len(req["day"]) == n
        assert req["day"][0] == "01"
        assert req["day"][-1] == "%02d" % n


def test_control_request_differs_only_in_forecast_type(cfg, tigge):
    groups = tigge.param_groups(cfg)
    pf = tigge.build_request(cfg, SeasonMonth(2022, 8), groups["precip"],
                             "perturbed_forecast")
    cf = tigge.build_request(cfg, SeasonMonth(2022, 8), groups["precip"],
                             "control_forecast")
    assert cf["forecast_type"] == "control_forecast"
    # ECDS has no member-count key; the forecast type alone selects members.
    assert "number" not in cf
    assert {k: v for k, v in pf.items() if k != "forecast_type"} ==            {k: v for k, v in cf.items() if k != "forecast_type"}


def test_pressure_level_request_sets_level_value(cfg, tigge):
    groups = tigge.param_groups(cfg)
    req = tigge.build_request(cfg, SeasonMonth(2022, 7), groups["pressure"],
                              "perturbed_forecast")
    assert req["level_type"] == "pressure"
    assert req["level_value"] == ["850"]
    assert set(req["variable"]) == {"u_component_of_wind", "v_component_of_wind",
                                    "specific_humidity"}


def test_single_level_requests_have_no_level_value(cfg, tigge):
    groups = tigge.param_groups(cfg)
    for name in ("precip", "surface"):
        req = tigge.build_request(cfg, SeasonMonth(2022, 7), groups[name],
                                  "perturbed_forecast")
        assert req["level_type"] == "single_level"
        assert "level_value" not in req


def test_surface_group_has_the_listed_variables(cfg, tigge):
    groups = tigge.param_groups(cfg)
    assert set(groups["surface"]["variables"]) == {
        "total_column_water",
        "convective_available_potential_energy",
        "convective_inhibition",
        "mean_sea_level_pressure",
        "10_m_u_component_of_wind",
        "10_m_v_component_of_wind",
    }


def test_variables_are_valid_ecds_names(cfg, tigge):
    """ECDS uses long names, not the MARS numeric parameter codes."""
    for group in tigge.param_groups(cfg).values():
        for v in group["variables"]:
            assert not v.isdigit(), "MARS code left in the config: " + v
            assert v == v.lower()


def test_target_paths_are_unique_per_request(cfg, tigge):
    seen = set()
    for month in (SeasonMonth(2022, 6), SeasonMonth(2022, 7)):
        for group in ("precip", "surface", "pressure"):
            for fc_type in ("perturbed_forecast", "control_forecast"):
                path = tigge.target_path(cfg, group, fc_type, month)
                assert path not in seen, "target collision: " + str(path)
                seen.add(path)
    assert len(seen) == 12


# --- IMERG ------------------------------------------------------------------

def test_imerg_script_uses_the_crop_box_not_the_region(cfg):
    """The downloader must request the wider crop box, not the 30-37/69-75 box.

    The GES DISC generated script shipped with the project had the wrong box
    entirely (37.1,23.7,60.9,77.8, which is west of Pakistan), so this is
    checked explicitly.
    """
    from src.core.config import crop_bbox_wsen
    west, south, east, north = crop_bbox_wsen(cfg)
    assert (west, south, east, north) == (68.75, 29.75, 75.25, 37.25)
    # The region itself must sit strictly inside the requested box.
    r = cfg["region"]
    assert west < r["west"] and east > r["east"]
    assert south < r["south"] and north > r["north"]


# --- IMERG OPeNDAP subsetting ----------------------------------------------

@pytest.fixture(scope="module")
def imerg():
    return _load_script("download_imerg")


GRANULE = "3B-DAY.MS.MRG.3IMERG.20220801-S000000-E235959.V07B.nc4"


def test_grid_indices_match_the_crop_box(cfg, imerg):
    """Index ranges were verified against the real grid: lat 1197-1272,
    lon 2487-2552, giving the 76 x 66 subset."""
    lat0, lat1, lon0, lon1 = imerg.grid_indices(cfg)
    assert (lat0, lat1) == (1197, 1272)
    assert (lon0, lon1) == (2487, 2552)
    assert lat1 - lat0 + 1 == 76
    assert lon1 - lon0 + 1 == 66


def test_grid_indices_round_trip_to_coordinates(cfg, imerg):
    """Converting indices back to degrees must land on the crop box."""
    lat0, lat1, lon0, lon1 = imerg.grid_indices(cfg)
    c = cfg["region"]["crop"]
    south = imerg.IMERG_LAT0 + lat0 * imerg.IMERG_STEP
    north = imerg.IMERG_LAT0 + lat1 * imerg.IMERG_STEP
    west = imerg.IMERG_LON0 + lon0 * imerg.IMERG_STEP
    east = imerg.IMERG_LON0 + lon1 * imerg.IMERG_STEP
    assert south == pytest.approx(c["south"], abs=1e-6)
    assert north == pytest.approx(c["north"], abs=1e-6)
    assert west == pytest.approx(c["west"], abs=1e-6)
    assert east == pytest.approx(c["east"], abs=1e-6)


def test_granule_date_parsing(imerg):
    assert imerg.granule_date(GRANULE) == "20220801"
    assert imerg.granule_date("nonsense.nc4") is None


def test_opendap_url_has_lon_before_lat(cfg, imerg):
    """IMERG stores precipitation as (time, lon, lat). Swapping the two index
    ranges returns a transposed grid that still looks plausible."""
    url = imerg.opendap_url(GRANULE, cfg)
    assert url.startswith(imerg.OPENDAP_BASE + "/2022/08/" + GRANULE + ".nc4?")
    assert "precipitation[0:0][2487:2552][1197:1272]" in url
    assert "randomError[0:0][2487:2552][1197:1272]" in url
    assert "lat[1197:1272]" in url
    assert "lon[2487:2552]" in url


def test_opendap_url_rejects_unparsable_granule(cfg, imerg):
    with pytest.raises(ValueError, match="cannot read a date"):
        imerg.opendap_url("not-a-granule.nc4", cfg)
