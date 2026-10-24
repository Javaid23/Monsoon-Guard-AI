"""Put IMERG observations onto the TIGGE forecast grid, and match time windows.

Two rules from CLAUDE.md:

- Section 6.2: average the IMERG 0.1 degree cells inside each 0.5 degree box.
  Never regrid the forecast to the observation grid.
- Section 6.1: a forecast issued at 00 UTC on D covers D to D+1 for lead day 1,
  and lead day L is verified by the IMERG daily file for date D + (L - 1).

The averaging is area-weighted. IMERG cell centres sit at 0.05 degree offsets,
so the edges of a 0.5 degree box land exactly on cell centres; those cells are
shared with the neighbouring box and get half weight. Counting them fully in
both boxes would inflate every box to 36 cells instead of 25.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import xarray as xr

from .config import Config, load_config

EPS = 1e-9


# --- time windows -----------------------------------------------------------

@dataclass(frozen=True)
class Window:
    """The period a forecast covers, and the observation date verifying it."""
    issue_date: date
    lead_day: int

    @property
    def window_start_utc(self) -> datetime:
        return datetime(self.issue_date.year, self.issue_date.month,
                        self.issue_date.day) + timedelta(days=self.lead_day - 1)

    @property
    def window_end_utc(self) -> datetime:
        return self.window_start_utc + timedelta(days=1)

    @property
    def valid_date(self) -> date:
        """The IMERG daily file that verifies this window."""
        return self.window_start_utc.date()

    def as_columns(self) -> dict:
        """The explicit columns CLAUDE.md section 6.1 requires."""
        return {
            "issue_date": self.issue_date.isoformat(),
            "lead_day": self.lead_day,
            "window_start_utc": self.window_start_utc.isoformat() + "Z",
            "window_end_utc": self.window_end_utc.isoformat() + "Z",
            "valid_date": self.valid_date.isoformat(),
        }


def windows_for(issue_date: date, cfg: Config | None = None) -> list[Window]:
    cfg = cfg or load_config()
    return [Window(issue_date, lead) for lead in cfg["forecast"]["lead_days"]]


# --- spatial averaging ------------------------------------------------------

def build_weights(source_coords: np.ndarray, target_coords: np.ndarray,
                  half_width: float) -> np.ndarray:
    """Weight matrix mapping one source axis onto one target axis.

    Shape (n_target, n_source). A source cell centre strictly inside a target
    box gets weight 1; one lying exactly on a box edge gets 0.5, because it is
    shared with the adjacent box. Rows are normalised to sum to 1.
    """
    src = np.asarray(source_coords, dtype="float64")
    tgt = np.asarray(target_coords, dtype="float64")

    distance = np.abs(src[None, :] - tgt[:, None])
    inside = distance < half_width - EPS
    on_edge = np.abs(distance - half_width) < EPS

    weights = np.where(inside, 1.0, 0.0) + np.where(on_edge, 0.5, 0.0)

    totals = weights.sum(axis=1)
    empty = totals <= 0
    if empty.any():
        missing = tgt[empty]
        raise ValueError(
            "no source cells within %.3f deg of target coordinate(s) %s - the "
            "crop box is too small for this grid"
            % (half_width, np.array2string(missing, precision=2)))
    return weights / totals[:, None]


def regrid_to_forecast(obs: xr.DataArray, target_lat, target_lon,
                       cfg: Config | None = None) -> xr.DataArray:
    """Average a (…, lat, lon) observation array onto the forecast grid.

    The forecast grid is never touched: observations move to it, not the
    reverse (CLAUDE.md section 6.2).
    """
    cfg = cfg or load_config()
    half = float(cfg["region"]["grid_resolution_deg"]) / 2.0

    target_lat = np.asarray(target_lat, dtype="float64")
    target_lon = np.asarray(target_lon, dtype="float64")

    w_lat = build_weights(obs["lat"].values, target_lat, half)
    w_lon = build_weights(obs["lon"].values, target_lon, half)

    values = obs.values.astype("float64")

    # NaN-aware weighted average. A plain contraction would be wrong: zero
    # weights do not exclude a NaN, because 0 * NaN is NaN, so one missing
    # observation anywhere would propagate through the whole contraction and
    # turn EVERY output cell into NaN. Instead, treat missing values as
    # absent and renormalise by the weight that actually contributed.
    missing = ~np.isfinite(values)
    if missing.any():
        present = np.where(missing, 0.0, values)
        valid = (~missing).astype("float64")
        weighted = np.einsum("il,...lm,jm->...ij", w_lat, present, w_lon)
        coverage = np.einsum("il,...lm,jm->...ij", w_lat, valid, w_lon)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(coverage > 0, weighted / coverage, np.nan)
    else:
        out = np.einsum("il,...lm,jm->...ij", w_lat, values, w_lon)

    dims = list(obs.dims[:-2]) + ["latitude", "longitude"]
    coords = {d: obs.coords[d].values for d in obs.dims[:-2] if d in obs.coords}
    coords["latitude"] = target_lat
    coords["longitude"] = target_lon

    return xr.DataArray(out, dims=dims, coords=coords,
                        attrs=dict(obs.attrs,
                                   regridding="area-weighted 0.1 to 0.5 deg"))


def effective_cell_counts(obs_lat, obs_lon, target_lat, target_lon,
                          cfg: Config | None = None) -> np.ndarray:
    """Unnormalised weight sum per target box. Should be 25.0 for 0.1 -> 0.5."""
    cfg = cfg or load_config()
    half = float(cfg["region"]["grid_resolution_deg"]) / 2.0

    def raw(src, tgt):
        src = np.asarray(src, dtype="float64")
        tgt = np.asarray(tgt, dtype="float64")
        d = np.abs(src[None, :] - tgt[:, None])
        return (np.where(d < half - EPS, 1.0, 0.0)
                + np.where(np.abs(d - half) < EPS, 0.5, 0.0))

    return np.outer(raw(obs_lat, target_lat).sum(axis=1),
                    raw(obs_lon, target_lon).sum(axis=1))
