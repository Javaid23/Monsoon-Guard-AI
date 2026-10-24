"""Forecast verification metrics for the underlying forecast (CLAUDE.md 8.2).

These describe how good the *forecast* is, independent of our error models:
probability of detection, false alarm ratio, critical success index, miss
rate and frequency bias, from the 2x2 contingency table of signal against
observed event.

Also here: the block bootstrap by calendar week that section 8.1 requires,
because cells and consecutive days are strongly correlated and treating rows
as independent would produce confidence intervals that are far too narrow.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ContingencyTable:
    """2x2 table of forecast signal against observed event."""
    hits: int               # signal=1, event=1
    false_alarms: int       # signal=1, event=0
    misses: int             # signal=0, event=1
    correct_negatives: int  # signal=0, event=0

    @property
    def n(self) -> int:
        return self.hits + self.false_alarms + self.misses + self.correct_negatives

    @property
    def probability_of_detection(self) -> float:
        d = self.hits + self.misses
        return self.hits / d if d else float("nan")

    @property
    def false_alarm_ratio(self) -> float:
        d = self.hits + self.false_alarms
        return self.false_alarms / d if d else float("nan")

    @property
    def critical_success_index(self) -> float:
        d = self.hits + self.false_alarms + self.misses
        return self.hits / d if d else float("nan")

    @property
    def miss_rate(self) -> float:
        d = self.hits + self.misses
        return self.misses / d if d else float("nan")

    @property
    def frequency_bias(self) -> float:
        d = self.hits + self.misses
        return (self.hits + self.false_alarms) / d if d else float("nan")

    def as_dict(self) -> dict:
        return {
            "hits": self.hits, "false_alarms": self.false_alarms,
            "misses": self.misses, "correct_negatives": self.correct_negatives,
            "n": self.n,
            "probability_of_detection": self.probability_of_detection,
            "false_alarm_ratio": self.false_alarm_ratio,
            "critical_success_index": self.critical_success_index,
            "miss_rate": self.miss_rate,
            "frequency_bias": self.frequency_bias,
        }

    def report(self, label: str = "") -> str:
        return ("%-18s POD=%.3f  FAR=%.3f  CSI=%.3f  miss=%.3f  bias=%.3f  "
                "(H=%d FA=%d M=%d CN=%d)"
                % (label, self.probability_of_detection, self.false_alarm_ratio,
                   self.critical_success_index, self.miss_rate,
                   self.frequency_bias, self.hits, self.false_alarms,
                   self.misses, self.correct_negatives))


def contingency(signal: np.ndarray, y_event: np.ndarray) -> ContingencyTable:
    s = np.asarray(signal).astype(bool)
    e = np.asarray(y_event).astype(bool)
    return ContingencyTable(
        hits=int((s & e).sum()),
        false_alarms=int((s & ~e).sum()),
        misses=int((~s & e).sum()),
        correct_negatives=int((~s & ~e).sum()),
    )


def contingency_by_signal_level(member_fraction: np.ndarray, y_event: np.ndarray,
                                levels) -> dict:
    """Verification at several signal levels (CLAUDE.md 8.2 asks for 10/20/30/50%)."""
    frac = np.asarray(member_fraction, dtype="float64")
    return {str(level): contingency(frac >= level, y_event).as_dict()
            for level in levels}


# --- block bootstrap ----------------------------------------------------------

def calendar_week(dates) -> np.ndarray:
    """ISO year-week label per row, the resampling unit for the bootstrap."""
    d = pd.to_datetime(pd.Series(list(dates)))
    iso = d.dt.isocalendar()
    return (iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)).values


def block_bootstrap_ci(values: np.ndarray, blocks: np.ndarray,
                       statistic=np.mean, n_resamples: int = 1000,
                       alpha: float = 0.05, seed: int = 0) -> dict:
    """Confidence interval by resampling whole blocks (calendar weeks).

    Resampling individual rows would treat strongly correlated neighbouring
    cells and days as independent and give intervals that are far too narrow
    (CLAUDE.md sections 8.1 and 15).
    """
    values = np.asarray(values, dtype="float64")
    blocks = np.asarray(blocks)
    unique = np.unique(blocks)
    index_by_block = {b: np.flatnonzero(blocks == b) for b in unique}

    rng = np.random.default_rng(seed)
    stats = np.empty(n_resamples, dtype="float64")
    for i in range(n_resamples):
        drawn = rng.choice(unique, size=len(unique), replace=True)
        idx = np.concatenate([index_by_block[b] for b in drawn])
        stats[i] = statistic(values[idx])

    lo, hi = np.quantile(stats, [alpha / 2, 1 - alpha / 2])
    return {
        "point_estimate": float(statistic(values)),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "alpha": alpha,
        "n_resamples": n_resamples,
        "n_blocks": int(len(unique)),
    }
