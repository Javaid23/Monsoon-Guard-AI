"""Extreme-event thresholds, forecast signals, and the two error targets.

CLAUDE.md section 6.3, per cell:

1. `y_event`  = 1 if the 0.5 degree IMERG daily total exceeds that cell's
   threshold. The threshold is the 95th percentile of JJAS daily IMERG over
   the **training years only**.
2. `signal`   = 1 if the fraction of perturbed members exceeding the same
   threshold is at least the signal level (default 0.20).
3. If `signal` = 1, `false_alarm` = 1 when `y_event` = 0.
   If `signal` = 0, `miss`        = 1 when `y_event` = 1.

The threshold is the one place where leakage is easiest to introduce and
hardest to notice, so `compute_thresholds` refuses to look at any year outside
the training split unless explicitly told to, and records which years it used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from .align import Window
from .config import Config, load_config, split_months


@dataclass
class Thresholds:
    """Per-cell extreme-rainfall thresholds and the provenance to prove them."""
    values: xr.DataArray                  # (latitude, longitude), mm
    percentile: float
    years_used: list[int] = field(default_factory=list)
    n_days_used: int = 0
    months_used: list[int] = field(default_factory=list)

    def report(self) -> str:
        v = self.values.values
        return "\n".join([
            "thresholds: p%g of daily rainfall per cell" % self.percentile,
            "  computed from years %s, months %s, %d days"
            % (self.years_used, self.months_used, self.n_days_used),
            "  grid %s, range %.2f to %.2f mm, median %.2f mm"
            % (tuple(v.shape), float(np.nanmin(v)), float(np.nanmax(v)),
               float(np.nanmedian(v))),
        ])


def compute_thresholds(obs: xr.DataArray, cfg: Config | None = None,
                       years: list[int] | None = None,
                       months: list[str] | None = None,
                       percentile: float | None = None) -> Thresholds:
    """Per-cell percentile of daily rainfall, from the TRAIN split only.

    `obs` must already be on the forecast grid, with dims
    (time, latitude, longitude).

    By default the threshold uses exactly the configured `train_months`.
    `months` (a list of "YYYY-MM") or `years` may override that, but only for
    sensitivity runs and the case study; whatever was actually used is
    recorded on the result so a later check can prove no calibration or test
    month contributed.
    """
    cfg = cfg or load_config()
    percentile = (percentile if percentile is not None
                  else float(cfg["labels"]["threshold_percentile"]))

    if {"latitude", "longitude"} - set(obs.dims):
        raise ValueError(
            "thresholds must be computed on the forecast grid (latitude, "
            "longitude); got dims %s" % (obs.dims,))

    time_index = pd.to_datetime(obs["time"].values)
    stamps = time_index.strftime("%Y-%m")

    if months is None and years is None:
        months = split_months(cfg, "train")

    if months is not None:
        mask = np.isin(stamps, list(months))
        label = "months %s" % (list(months),)
    else:
        season = list(cfg["season"]["months"])
        mask = np.isin(time_index.year, years) & np.isin(time_index.month, season)
        label = "years %s" % (list(years),)

    if not mask.any():
        raise ValueError("no observations in " + label)

    subset = obs.isel(time=np.flatnonzero(mask))
    values = subset.quantile(percentile / 100.0, dim="time").drop_vars(
        "quantile", errors="ignore")
    values.name = "threshold_mm"

    return Thresholds(
        values=values,
        percentile=percentile,
        years_used=sorted({int(y) for y in time_index.year[mask]}),
        n_days_used=int(mask.sum()),
        months_used=sorted(set(stamps[mask])),
    )


def observed_events(obs: xr.DataArray, thresholds: Thresholds) -> xr.DataArray:
    """y_event: 1 where observed rainfall exceeds the cell threshold."""
    events = (obs > thresholds.values).astype("int8")
    events.name = "y_event"
    return events


def member_fraction_over(members: xr.DataArray, thresholds: Thresholds,
                         member_dim: str = "number") -> xr.DataArray:
    """Fraction of ensemble members exceeding each cell's threshold.

    A fraction, never a count: ensemble size can differ between years and a
    count would not be comparable across them (CLAUDE.md section 5.2).
    """
    over = (members > thresholds.values)
    frac = over.mean(dim=member_dim)
    frac.name = "member_fraction"
    return frac.astype("float32")


def forecast_signal(member_fraction: xr.DataArray, cfg: Config | None = None,
                    signal_fraction: float | None = None) -> xr.DataArray:
    """signal: 1 where at least `signal_fraction` of members exceed threshold."""
    cfg = cfg or load_config()
    level = (signal_fraction if signal_fraction is not None
             else float(cfg["labels"]["signal_fraction"]))
    signal = (member_fraction >= level).astype("int8")
    signal.name = "signal"
    signal.attrs["signal_fraction"] = level
    return signal


def error_targets(signal: np.ndarray, y_event: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray]:
    """The two conditional targets (CLAUDE.md section 7.1).

    Each sample belongs to exactly one subset. Outside its own subset a target
    is NaN, not zero: model A trains only where signal = 1 and model B only
    where signal = 0, so a zero would silently add fabricated negatives.
    """
    signal = np.asarray(signal)
    y_event = np.asarray(y_event)

    false_alarm = np.full(signal.shape, np.nan, dtype="float32")
    miss = np.full(signal.shape, np.nan, dtype="float32")

    is_signal = signal == 1
    false_alarm[is_signal] = (y_event[is_signal] == 0).astype("float32")
    miss[~is_signal] = (y_event[~is_signal] == 1).astype("float32")

    return false_alarm, miss


def build_label_table(forecast: xr.DataArray, obs: xr.DataArray,
                      thresholds: Thresholds, cfg: Config | None = None,
                      signal_fraction: float | None = None) -> pd.DataFrame:
    """One row per (issue_date, lead_day, cell).

    `forecast` is (number, time, lead_day, latitude, longitude) of daily window
    totals; `obs` is (time, latitude, longitude) on the same grid. Issue dates
    whose verifying observation is missing are dropped and counted, never
    filled in.
    """
    cfg = cfg or load_config()

    fraction = member_fraction_over(forecast, thresholds)
    signal = forecast_signal(fraction, cfg, signal_fraction)

    # A duplicate observation date would silently resolve to whichever row
    # came last, quietly changing every label for that day. Refuse instead
    # (CLAUDE.md section 2.12: report data problems, never absorb them).
    obs_index = pd.to_datetime(obs["time"].values)
    obs_dates: dict = {}
    duplicates: list[str] = []
    for i, d in enumerate(obs_index):
        day = d.date()
        if day in obs_dates:
            duplicates.append(day.isoformat())
        obs_dates[day] = i
    if duplicates:
        raise ValueError(
            "observations contain %d duplicate date(s), e.g. %s. Labels "
            "cannot be built from an ambiguous archive - deduplicate the "
            "IMERG files first."
            % (len(duplicates), ", ".join(sorted(set(duplicates))[:5])))
    lats = forecast["latitude"].values
    lons = forecast["longitude"].values
    issue_dates = pd.to_datetime(forecast["time"].values)

    frames = []
    missing_obs: list[tuple[str, int]] = []

    for lead in [int(x) for x in forecast["lead_day"].values]:
        frac_lead = fraction.sel(lead_day=lead).values       # (time, lat, lon)
        sig_lead = signal.sel(lead_day=lead).values

        for t, issued in enumerate(issue_dates):
            window = Window(issued.date(), lead)
            idx = obs_dates.get(window.valid_date)
            if idx is None:
                missing_obs.append((window.issue_date.isoformat(), lead))
                continue

            observed = obs.isel(time=idx).values
            y_event = (observed > thresholds.values.values).astype("int8")
            fa, miss = error_targets(sig_lead[t], y_event)

            cols = window.as_columns()
            frames.append(pd.DataFrame({
                "issue_date": cols["issue_date"],
                "lead_day": lead,
                "window_start_utc": cols["window_start_utc"],
                "window_end_utc": cols["window_end_utc"],
                "valid_date": cols["valid_date"],
                "latitude": np.repeat(lats, len(lons)),
                "longitude": np.tile(lons, len(lats)),
                "obs_mm": observed.ravel(),
                "threshold_mm": thresholds.values.values.ravel(),
                "member_fraction": frac_lead[t].ravel(),
                "signal": sig_lead[t].ravel(),
                "y_event": y_event.ravel(),
                "false_alarm": fa.ravel(),
                "miss": miss.ravel(),
            }))

    if not frames:
        raise ValueError("no (issue_date, lead_day) pair had a verifying "
                         "observation; check the IMERG date coverage")

    table = pd.concat(frames, ignore_index=True)
    table.attrs["missing_observations"] = missing_obs
    table.attrs["threshold_percentile"] = thresholds.percentile
    table.attrs["threshold_years"] = thresholds.years_used
    table.attrs["signal_fraction"] = float(signal.attrs["signal_fraction"])
    return table


def base_rates(table: pd.DataFrame) -> dict:
    """Counts and base rates, including the two conditional subsets."""
    n = len(table)
    n_signal = int((table["signal"] == 1).sum())
    n_quiet = n - n_signal
    fa = table.loc[table["signal"] == 1, "false_alarm"]
    ms = table.loc[table["signal"] == 0, "miss"]
    return {
        "n_samples": n,
        "n_event": int(table["y_event"].sum()),
        "event_rate": float(table["y_event"].mean()) if n else float("nan"),
        "n_signal": n_signal,
        "signal_rate": n_signal / n if n else float("nan"),
        "n_false_alarm": int(fa.sum()) if len(fa) else 0,
        "false_alarm_rate": float(fa.mean()) if len(fa) else float("nan"),
        "n_miss": int(ms.sum()) if len(ms) else 0,
        "miss_rate": float(ms.mean()) if len(ms) else float("nan"),
        "n_quiet": n_quiet,
    }


def format_base_rates(rates: dict) -> str:
    return "\n".join([
        "samples %s   events %s (%.3f%%)" % (
            format(rates["n_samples"], ","), format(rates["n_event"], ","),
            100 * rates["event_rate"]),
        "  signal=1 subset: %s samples, %s false alarms (%.1f%%)" % (
            format(rates["n_signal"], ","), format(rates["n_false_alarm"], ","),
            100 * rates["false_alarm_rate"]) if rates["n_signal"] else
        "  signal=1 subset: empty",
        "  signal=0 subset: %s samples, %s misses (%.3f%%)" % (
            format(rates["n_quiet"], ","), format(rates["n_miss"], ","),
            100 * rates["miss_rate"]) if rates["n_quiet"] else
        "  signal=0 subset: empty",
    ])
