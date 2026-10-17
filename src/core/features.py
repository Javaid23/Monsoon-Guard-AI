"""Feature engineering for the error models (CLAUDE.md section 7.2).

Every feature here is computable at the forecast issue time: nothing looks at
observations, nothing looks past the issue date. Features fall into four
groups:

  ensemble      mean, std, max, quantiles, fraction over threshold/half
                threshold - all from the perturbed member distribution.
  neighbourhood mean and max of the ensemble mean over the surrounding 3x3
                cells.
  context       lead day, day of year, cell latitude and longitude.
  monsoon       OMI PC1, PC2, amplitude and a derived phase, at or before the
                issue date (section 5.3: never a later date).

Atmospheric fields (CAPE, CIN, total column water, MSLP, 850 hPa wind and
humidity) are added once those groups are downloaded; this module is written
so adding them later means adding columns, not restructuring the table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from .config import Config, load_config

ENSEMBLE_QUANTILES = (0.25, 0.50, 0.75, 0.90)
SPREAD_EPS = 1e-3          # avoids divide-by-zero when normalising spread


# --- ensemble features -------------------------------------------------------

def ensemble_features(members: xr.DataArray, threshold: xr.DataArray,
                      member_dim: str = "number") -> dict[str, xr.DataArray]:
    """Per-(time, lead_day, cell) summaries of the member distribution.

    `members` is (number, time, lead_day, latitude, longitude). `threshold`
    is (latitude, longitude). Returns a dict of DataArrays, each
    (time, lead_day, latitude, longitude).
    """
    mean = members.mean(dim=member_dim)
    std = members.std(dim=member_dim)
    out = {
        "ens_mean": mean,
        "ens_std": std,
        "ens_max": members.max(dim=member_dim),
        "ens_std_norm": std / (mean + SPREAD_EPS),
        "ens_frac_over_threshold": (members > threshold).mean(dim=member_dim),
        "ens_frac_over_half_threshold": (members > threshold / 2).mean(dim=member_dim),
    }
    for q in ENSEMBLE_QUANTILES:
        out["ens_q%02d" % int(q * 100)] = members.quantile(q, dim=member_dim).drop_vars(
            "quantile", errors="ignore")
    return out


# --- neighbourhood features --------------------------------------------------

def neighbourhood_features(field: xr.DataArray, cfg: Config | None = None
                           ) -> dict[str, xr.DataArray]:
    """Mean and max of `field` over the surrounding 3x3 cells.

    `field` must have `latitude` and `longitude` as its last two dimensions
    (true of every feature array this module builds). Edge cells use whatever
    neighbours exist (a 2x3 or 2x2 window), never wrapping around the grid.
    """
    cfg = cfg or load_config()
    assert field.dims[-2:] == ("latitude", "longitude"), (
        "neighbourhood_features requires latitude, longitude as the last "
        "two dimensions, got " + str(field.dims))

    values = field.values              # (..., n_lat, n_lon)
    n_lat, n_lon = values.shape[-2:]

    mean_out = np.empty_like(values, dtype="float64")
    max_out = np.empty_like(values, dtype="float64")

    for i in range(n_lat):
        i0, i1 = max(0, i - 1), min(n_lat, i + 2)
        for j in range(n_lon):
            j0, j1 = max(0, j - 1), min(n_lon, j + 2)
            window = values[..., i0:i1, j0:j1]          # (..., <=3, <=3)
            mean_out[..., i, j] = np.nanmean(window, axis=(-2, -1))
            max_out[..., i, j] = np.nanmax(window, axis=(-2, -1))

    coords = dict(field.coords)
    return {
        "neigh_mean": xr.DataArray(mean_out, dims=field.dims, coords=coords),
        "neigh_max": xr.DataArray(max_out, dims=field.dims, coords=coords),
    }


# --- context features --------------------------------------------------------

def context_features(issue_dates: pd.DatetimeIndex, lead_days: list[int],
                     lat: np.ndarray, lon: np.ndarray) -> pd.DataFrame:
    """Lead day, day of year, latitude and longitude for every sample.

    Returns a long table with one row per (issue_date, lead_day, lat, lon),
    in the same row order `labels.build_label_table` produces (lead day outer,
    then issue date, then lat, then lon) so the two can be joined by position.
    """
    rows = []
    for lead in lead_days:
        for d in issue_dates:
            n = len(lat) * len(lon)
            rows.append(pd.DataFrame({
                "issue_date": d.date().isoformat(),
                "lead_day": lead,
                "day_of_year": d.dayofyear,
                "latitude": np.repeat(lat, len(lon)),
                "longitude": np.tile(lon, len(lat)),
            }))
    return pd.concat(rows, ignore_index=True)


# --- monsoon (OMI) features --------------------------------------------------

def omi_features(issue_dates: pd.DatetimeIndex, omi_path) -> pd.DataFrame:
    """OMI PC1, PC2, amplitude and derived phase at each issue date.

    Uses the value dated on or before the issue date, per CLAUDE.md section
    5.3: never a date after the forecast was issued. Missing values are NaN,
    never silently filled with zero or a neighbouring day's value.
    """
    rows, bad = [], 0
    with open(omi_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 6:
                continue
            offset = 3 if len(parts) == 6 else 4
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                pc1 = float(parts[offset])
                pc2 = float(parts[offset + 1])
                amp = float(parts[offset + 2])
            except (ValueError, IndexError):
                bad += 1
                continue
            rows.append((pd.Timestamp(y, m, d), pc1, pc2, amp))

    table = pd.DataFrame(rows, columns=["date", "omi_pc1", "omi_pc2", "omi_amplitude"])
    table = table.sort_values("date").reset_index(drop=True)

    # Phase: the eight-sector convention used for RMM/OMI-style indices.
    angle = np.degrees(np.arctan2(table["omi_pc2"], table["omi_pc1"]))
    table["omi_phase"] = (((angle + 202.5) % 360) // 45 + 1).astype("int64")
    # Low amplitude means no coherent MJO signal; phase is not meaningful.
    table.loc[table["omi_amplitude"] < 1.0, "omi_phase"] = 0

    out = []
    for d in issue_dates:
        prior = table[table["date"] <= d]
        if prior.empty:
            out.append({"omi_pc1": np.nan, "omi_pc2": np.nan,
                       "omi_amplitude": np.nan, "omi_phase": np.nan,
                       "omi_date_used": None})
        else:
            row = prior.iloc[-1]
            out.append({"omi_pc1": row["omi_pc1"], "omi_pc2": row["omi_pc2"],
                       "omi_amplitude": row["omi_amplitude"],
                       "omi_phase": row["omi_phase"],
                       "omi_date_used": row["date"].date().isoformat()})
    return pd.DataFrame(out)


def build_feature_table(forecast: xr.DataArray, threshold: xr.DataArray,
                        omi_path=None, cfg: Config | None = None
                        ) -> pd.DataFrame:
    """Assemble every feature into one long table.

    `forecast` is (number, time, lead_day, latitude, longitude). Row order
    matches `labels.build_label_table`: lead day outer, then issue date, then
    latitude, then longitude - so the two tables can be joined on
    (issue_date, lead_day, latitude, longitude) or, when built from the same
    `forecast` array, concatenated by position.
    """
    cfg = cfg or load_config()

    ens = ensemble_features(forecast, threshold)
    neigh = neighbourhood_features(ens["ens_mean"], cfg)
    ens_and_neigh = {**ens, **neigh}

    lat = forecast["latitude"].values
    lon = forecast["longitude"].values
    issue_dates = pd.to_datetime(forecast["time"].values)
    lead_days = [int(x) for x in forecast["lead_day"].values]

    frames = []
    for lead in lead_days:
        block = {name: arr.sel(lead_day=lead).values.reshape(len(issue_dates), -1)
                for name, arr in ens_and_neigh.items()}
        n_cells = len(lat) * len(lon)
        df = pd.DataFrame({
            name: values.ravel() for name, values in block.items()
        })
        df.insert(0, "lead_day", lead)
        df.insert(0, "issue_date",
                  np.repeat([d.date().isoformat() for d in issue_dates], n_cells))
        df["latitude"] = np.tile(np.repeat(lat, len(lon)), len(issue_dates))
        df["longitude"] = np.tile(np.tile(lon, len(lat)), len(issue_dates))
        frames.append(df)

    table = pd.concat(frames, ignore_index=True)
    table["day_of_year"] = pd.to_datetime(table["issue_date"]).dt.dayofyear

    if omi_path is not None:
        table = attach_omi(table, omi_path)

    return table


def attach_omi(table: pd.DataFrame, omi_path) -> pd.DataFrame:
    """Join OMI features onto a label table by issue_date, one lookup per
    distinct date rather than once per row."""
    dates = pd.to_datetime(sorted(table["issue_date"].unique()))
    omi = omi_features(dates, omi_path)
    omi["issue_date"] = [d.date().isoformat() for d in dates]
    return table.merge(omi, on="issue_date", how="left")
