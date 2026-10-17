"""Load TIGGE NCEP ensemble forecasts and turn accumulations into daily windows.

TIGGE precipitation is accumulated from the start of the forecast, so the
per-day totals are differences between lead times:

    day 1 = step 24
    day 2 = step 48 - step 24
    day 3 = step 72 - step 48

GRIB packing makes some of those differences very slightly negative. They are
clipped to zero and counted, never silently dropped (CLAUDE.md section 5.2).

Every file is checked on load: grid shape and resolution, latitude ordering,
units, step list and member count. ECDS has no request key for the grid, so
the resolution is an assumption that has to be verified per file rather than
guaranteed by the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import xarray as xr

from .config import Config, load_config

VARIABLE = "tp"
UNITS = "kg m**-2"          # numerically millimetres
STEP_TYPE = "accum"
NANOS_PER_HOUR = 3_600_000_000_000


@dataclass
class TiggeQC:
    """What a file actually contains, checked against the configuration."""
    path: str = ""
    n_members: int = 0
    n_times: int = 0
    steps_h: list[int] = field(default_factory=list)
    shape: tuple = ()
    lat_descending: bool = False
    grid_deg: float = float("nan")
    units: str = ""
    step_type: str = ""
    n_negative_clipped: int = 0
    most_negative: float = 0.0
    n_values: int = 0
    value_max: float = float("nan")

    @property
    def negative_share(self) -> float:
        return self.n_negative_clipped / self.n_values if self.n_values else 0.0

    def report(self) -> str:
        return "\n".join([
            "%s" % Path(self.path).name,
            "  members %d, issue dates %d, steps %s h" % (
                self.n_members, self.n_times, self.steps_h),
            "  grid %s at %.2f deg, latitudes %s" % (
                self.shape, self.grid_deg,
                "descending" if self.lat_descending else "ASCENDING"),
            "  units %r, stepType %r, max %.1f mm" % (
                self.units, self.step_type, self.value_max),
            "  negative differences clipped: %d of %d (%.4f%%), most negative %.4f mm"
            % (self.n_negative_clipped, self.n_values,
               100 * self.negative_share, self.most_negative),
        ])


def accumulations_to_windows(acc: np.ndarray, step_axis: int = 2
                             ) -> tuple[np.ndarray, int, float]:
    """Convert accumulated precipitation to per-window totals.

    `acc` holds values accumulated from the start of the forecast, with lead
    times increasing along `step_axis`. The first step is already a window
    total (hours 0 to 24); later windows are successive differences.

    Returns (windows, n_negative_clipped, most_negative). Negative values come
    from GRIB packing rounding; they are clipped to zero and counted so the
    count can be logged (CLAUDE.md section 5.2).
    """
    acc = np.asarray(acc, dtype="float64")
    windows = np.empty_like(acc)

    first = [slice(None)] * acc.ndim
    first[step_axis] = slice(0, 1)
    rest = [slice(None)] * acc.ndim
    rest[step_axis] = slice(1, None)

    windows[tuple(first)] = acc[tuple(first)]
    windows[tuple(rest)] = np.diff(acc, axis=step_axis)

    n_negative = int((windows < 0).sum())
    most_negative = float(windows.min()) if n_negative else 0.0
    np.clip(windows, 0.0, None, out=windows)
    return windows, n_negative, most_negative


def _check_structure(ds: xr.Dataset, cfg: Config, path: Path) -> dict:
    """Validate a loaded GRIB against the frozen configuration."""
    region = cfg["region"]
    fc = cfg["forecast"]

    if VARIABLE not in ds:
        raise ValueError("%s: no '%s' variable (have %s)"
                         % (path.name, VARIABLE, list(ds.data_vars)))
    da = ds[VARIABLE]

    units = str(da.attrs.get("GRIB_units", ""))
    if units != UNITS:
        raise ValueError("%s: units %r, expected %r" % (path.name, units, UNITS))

    step_type = str(da.attrs.get("GRIB_stepType", ""))
    if step_type != STEP_TYPE:
        raise ValueError("%s: stepType %r, expected %r"
                         % (path.name, step_type, STEP_TYPE))

    lat = ds.latitude.values
    lon = ds.longitude.values
    n_lat, n_lon = len(lat), len(lon)
    if [n_lat, n_lon] != list(region["expected_shape"]):
        raise ValueError("%s: grid %s, expected %s"
                         % (path.name, [n_lat, n_lon], region["expected_shape"]))

    # Latitudes must run 37 -> 30 (CLAUDE.md section 13).
    if not (lat[0] > lat[-1]):
        raise ValueError("%s: latitudes ascend (%.2f..%.2f); expected "
                         "descending north to south" % (path.name, lat[0], lat[-1]))

    grid_deg = float(abs(lat[1] - lat[0]))
    expected_deg = float(fc["expected_grid_deg"])
    if abs(grid_deg - expected_deg) > 1e-6:
        raise ValueError("%s: grid spacing %.4f deg, expected %.4f"
                         % (path.name, grid_deg, expected_deg))

    steps_h = [int(s / NANOS_PER_HOUR) for s in np.atleast_1d(ds.step.values)]
    if steps_h != list(fc["steps_h"]):
        raise ValueError("%s: steps %s h, expected %s"
                         % (path.name, steps_h, fc["steps_h"]))

    return {"units": units, "step_type": step_type, "steps_h": steps_h,
            "grid_deg": grid_deg, "lat_descending": True}


def load_file(path: Path | str, cfg: Config | None = None
              ) -> tuple[xr.DataArray, TiggeQC]:
    """Load one GRIB and return daily window totals as (number, time, lead_day,
    latitude, longitude) in millimetres, plus a QC record.

    The returned array holds per-day totals, not accumulations.
    """
    cfg = cfg or load_config()
    path = Path(path)

    # indexpath="" stops cfgrib writing a .idx cache file next to the GRIB:
    # data/raw is append-only (CLAUDE.md section 2.3).
    ds = xr.open_dataset(path, engine="cfgrib",
                         backend_kwargs={"indexpath": ""})
    try:
        meta = _check_structure(ds, cfg, path)
        da = ds[VARIABLE].load()

        # A control-forecast file has no member dimension; give it one so that
        # downstream code sees the same layout for both forecast types.
        if "number" not in da.dims:
            da = da.expand_dims(number=[0])
        da = da.transpose("number", "time", "step", "latitude", "longitude")

        windows, n_negative, most_negative = accumulations_to_windows(
            da.values, step_axis=2)

        qc = TiggeQC(
            path=str(path),
            n_members=da.sizes["number"],
            n_times=da.sizes["time"],
            shape=(da.sizes["latitude"], da.sizes["longitude"]),
            n_negative_clipped=n_negative,
            most_negative=most_negative,
            n_values=int(windows.size),
            value_max=float(windows.max()),
            **meta,
        )

        out = xr.DataArray(
            windows,
            dims=("number", "time", "lead_day", "latitude", "longitude"),
            coords={
                "number": da.number.values,
                "time": da.time.values,
                "lead_day": list(cfg["forecast"]["lead_days"]),
                "latitude": da.latitude.values,
                "longitude": da.longitude.values,
            },
            attrs={"units": "mm", "description":
                   "daily window total from accumulated tp, negatives clipped"},
        )
    finally:
        ds.close()

    return out, qc


def list_files(cfg: Config | None = None, group: str = "precip",
               fc_type: str = "pf") -> list[Path]:
    """GRIB paths for one parameter group and forecast type, sorted by name."""
    cfg = cfg or load_config()
    pattern = "%s_%s_*.grib" % (group, fc_type)
    return sorted(cfg.get_path("raw_tigge").glob(pattern))


def load(cfg: Config | None = None, group: str = "precip", fc_type: str = "pf",
         ) -> tuple[xr.DataArray, list[TiggeQC]]:
    """Load and concatenate every month available for a group and type."""
    cfg = cfg or load_config()
    paths = list_files(cfg, group, fc_type)
    if not paths:
        raise FileNotFoundError(
            "no %s_%s GRIB files in %s - run scripts/download_tigge.py"
            % (group, fc_type, cfg.get_path("raw_tigge")))

    arrays, qcs = [], []
    for p in paths:
        da, qc = load_file(p, cfg)
        arrays.append(da)
        qcs.append(qc)

    # Member counts can differ between years; refuse to concatenate silently.
    counts = {q.n_members for q in qcs}
    if len(counts) > 1:
        detail = ", ".join("%s=%d" % (Path(q.path).name, q.n_members) for q in qcs)
        raise ValueError(
            "member count differs between files, which would corrupt any "
            "count-based feature: " + detail)

    return xr.concat(arrays, dim="time").sortby("time"), qcs


if __name__ == "__main__":
    cfg = load_config()
    for p in list_files(cfg):
        _, qc = load_file(p, cfg)
        print(qc.report())
        print()
