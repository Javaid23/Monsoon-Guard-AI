"""Load cropped IMERG Final daily rainfall from data/raw/imerg_final/.

The downloader stores one netCDF per day, already subset to the crop box, with
the original granule file name so provenance is preserved. This module turns
those files into a single xarray object on the 0.1 degree grid, and reports
what it found rather than silently tolerating gaps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from .config import Config, load_config, season_months

VARIABLE = "precipitation"
UNITS = "mm/day"            # daily mean rate, numerically the daily total in mm
GRANULE_DATE = re.compile(r"3IMERG\.(\d{8})-S")


def granule_date(path: Path | str) -> date:
    """Date encoded in an IMERG granule file name."""
    m = GRANULE_DATE.search(Path(path).name)
    if not m:
        raise ValueError("not an IMERG granule name: " + Path(path).name)
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


@dataclass
class ImergQC:
    """What a load actually found. Printed and logged, never discarded."""
    n_files: int = 0
    first_date: date | None = None
    last_date: date | None = None
    expected_days: int = 0
    missing_dates: list[date] = field(default_factory=list)
    n_nan: int = 0
    value_min: float = float("nan")
    value_max: float = float("nan")
    units: str = ""
    shape: tuple = ()

    @property
    def complete(self) -> bool:
        return not self.missing_dates and self.n_files == self.expected_days

    def report(self) -> str:
        lines = [
            "IMERG: %d files, %s to %s" % (
                self.n_files,
                self.first_date.isoformat() if self.first_date else "-",
                self.last_date.isoformat() if self.last_date else "-"),
            "  grid %s, units %r" % (self.shape, self.units),
            "  values %.2f to %.2f mm, NaN %d" % (
                self.value_min, self.value_max, self.n_nan),
        ]
        if self.missing_dates:
            lines.append("  MISSING %d of %d expected days, first %s last %s"
                         % (len(self.missing_dates), self.expected_days,
                            self.missing_dates[0], self.missing_dates[-1]))
        else:
            lines.append("  complete: %d of %d expected days"
                         % (self.n_files, self.expected_days))
        return "\n".join(lines)


def list_files(cfg: Config | None = None, years: list[int] | None = None,
               months: list[int] | None = None) -> list[Path]:
    """Granule paths for the requested seasons, sorted by date."""
    cfg = cfg or load_config()
    paths = sorted(cfg.get_path("raw_imerg").glob("*3IMERG*.nc4"),
                   key=granule_date)
    if years is not None:
        paths = [p for p in paths if granule_date(p).year in set(years)]
    if months is not None:
        paths = [p for p in paths if granule_date(p).month in set(months)]
    return paths


def expected_dates(cfg: Config, years: list[int] | None = None) -> list[date]:
    """Every JJAS date the configuration asks for."""
    out = []
    for month in season_months(cfg, years):
        day = month.start
        while day <= month.end:
            out.append(day)
            day += timedelta(days=1)
    return out


def load(cfg: Config | None = None, years: list[int] | None = None,
         months: list[int] | None = None) -> tuple[xr.DataArray, ImergQC]:
    """Load IMERG daily rainfall as (time, lat, lon) in mm/day.

    Returns the data and a QC record. Missing days are reported in the QC
    record, never filled in or skipped quietly.
    """
    cfg = cfg or load_config()
    paths = list_files(cfg, years, months)
    if not paths:
        raise FileNotFoundError(
            "no IMERG granules in %s - run scripts/download_imerg.py"
            % cfg.get_path("raw_imerg"))

    arrays = []
    for p in paths:
        with xr.open_dataset(p, engine="h5netcdf") as ds:
            if VARIABLE not in ds:
                raise ValueError("%s: no '%s' variable" % (p.name, VARIABLE))
            da = ds[VARIABLE].load()
            # Files are stored as (time, lon, lat); use (time, lat, lon)
            # throughout so that every array in the project has one layout.
            da = da.transpose("time", "lat", "lon")
            arrays.append(da)

    data = xr.concat(arrays, dim="time").sortby("time")

    units = str(data.attrs.get("units", ""))
    if units and units != UNITS:
        raise ValueError("unexpected IMERG units %r (expected %r)" % (units, UNITS))

    found = {granule_date(p) for p in paths}
    wanted = expected_dates(cfg, years)
    if months is not None:
        wanted = [d for d in wanted if d.month in set(months)]

    values = data.values
    finite = np.isfinite(values)
    qc = ImergQC(
        n_files=len(paths),
        first_date=granule_date(paths[0]),
        last_date=granule_date(paths[-1]),
        expected_days=len(wanted),
        missing_dates=sorted(set(wanted) - found),
        n_nan=int((~finite).sum()),
        value_min=float(values[finite].min()) if finite.any() else float("nan"),
        value_max=float(values[finite].max()) if finite.any() else float("nan"),
        units=units or UNITS,
        shape=tuple(data.shape),
    )
    return data, qc


def load_day(day: date, cfg: Config | None = None) -> xr.DataArray:
    """Load a single day as (lat, lon). Raises if that day is not on disk."""
    cfg = cfg or load_config()
    matches = [p for p in list_files(cfg) if granule_date(p) == day]
    if not matches:
        raise FileNotFoundError("no IMERG granule for " + day.isoformat())
    with xr.open_dataset(matches[0], engine="h5netcdf") as ds:
        return ds[VARIABLE].isel(time=0).transpose("lat", "lon").load()


if __name__ == "__main__":
    data, qc = load()
    print(qc.report())
    print()
    print("area mean of the wettest day:")
    am = data.mean(dim=["lat", "lon"])
    i = int(am.argmax())
    print("  %s  %.2f mm" % (str(data.time.values[i])[:10], float(am[i])))
