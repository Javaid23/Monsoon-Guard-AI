"""The two conditional error models and the first rungs of the model ladder.

CLAUDE.md section 7.1: model A (false alarm) trains only where signal = 1,
target `false_alarm`; model B (miss) trains only where signal = 0, target
`miss`. This module keeps that separation throughout - the two subsets are
never mixed into one training set, and a sample never contributes to both.

Section 7.3's model ladder starts with a climatological reference and the raw
ensemble signal fraction before any fitted model. Both are implemented here as
baselines to compare the fitted models against, not as something to tune.

IMPORTANT: everything this module reports before the calibration year (2024)
and test year (2025) are downloaded is provisional. It uses leave-one-month-out
cross-validation across whatever training-year months are on disk, which is a
legitimate way to get an honest out-of-sample estimate from training data, but
it is not the CLAUDE.md section 8 evaluation protocol and must never be
reported as the project's headline result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS_ENSEMBLE = [
    "ens_mean", "ens_std", "ens_max", "ens_std_norm",
    "ens_frac_over_threshold", "ens_frac_over_half_threshold",
    "ens_q25", "ens_q50", "ens_q75", "ens_q90",
    "neigh_mean", "neigh_max",
]
FEATURE_COLUMNS_CONTEXT = ["lead_day", "day_of_year", "latitude", "longitude"]
FEATURE_COLUMNS_OMI = ["omi_pc1", "omi_pc2", "omi_amplitude"]


def feature_columns(table: pd.DataFrame, include_omi: bool = True) -> list[str]:
    """Feature columns actually present in `table`, in a fixed order."""
    cols = list(FEATURE_COLUMNS_ENSEMBLE) + list(FEATURE_COLUMNS_CONTEXT)
    if include_omi:
        cols += FEATURE_COLUMNS_OMI
    return [c for c in cols if c in table.columns]


@dataclass
class SubsetData:
    """One conditional model's training rows: X, y, the month each row
    belongs to (for blocked cross-validation), and the raw ensemble fraction
    (for the un-fitted baseline, which needs no features at all)."""
    X: np.ndarray
    y: np.ndarray
    month: np.ndarray           # "YYYY-MM" per row, for leave-one-month-out
    member_fraction: np.ndarray
    columns: list[str]
    n_dropped_nan: int = 0


def prepare_subset(table: pd.DataFrame, target: str, columns: list[str]
                   ) -> SubsetData:
    """Rows where `target` is defined (CLAUDE.md section 7.1: each row
    belongs to exactly one of the false-alarm or miss subsets)."""
    subset = table[table[target].notna()].copy()
    before = len(subset)
    subset = subset.dropna(subset=columns)
    dropped = before - len(subset)

    month = pd.to_datetime(subset["issue_date"]).dt.strftime("%Y-%m").values
    return SubsetData(
        X=subset[columns].to_numpy(dtype="float64"),
        y=subset[target].to_numpy(dtype="float64"),
        month=month,
        member_fraction=subset["ens_frac_over_threshold"].to_numpy(dtype="float64"),
        columns=columns,
        n_dropped_nan=dropped,
    )


# --- baselines ---------------------------------------------------------------

def reference_prediction(y_train: np.ndarray, n_test: int) -> np.ndarray:
    """Climatological reference: the training fold's base rate, for every row."""
    rate = float(y_train.mean()) if len(y_train) else float("nan")
    return np.full(n_test, rate)


def raw_reliability_prediction(member_fraction: np.ndarray, target: str,
                               signal_fraction: float) -> np.ndarray:
    """The ensemble signal fraction used directly as a naive p_wrong estimate.

    For the false-alarm subset (signal=1): a higher member fraction means
    more members agreed, so a false alarm is judged less likely -
    1 - member_fraction, rescaled into the subset's achievable range.
    For the miss subset (signal=0): a fraction closer to the signal threshold
    means a closer call, so a miss is judged more likely - member_fraction
    scaled by the signal threshold.

    This is a design choice for an otherwise-unspecified ladder rung, not a
    frozen decision; see docs/decisions.md.
    """
    if target == "false_alarm":
        return 1.0 - member_fraction
    return np.clip(member_fraction / signal_fraction, 0.0, 1.0)


# --- logistic regression ------------------------------------------------------

def fit_logistic(X_train: np.ndarray, y_train: np.ndarray):
    """Standardized logistic regression, class-weighted for the rarer class."""
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X_train)
    model = LogisticRegression(max_iter=2000, class_weight="balanced")
    model.fit(Xs, y_train)
    return scaler, model


def predict_logistic(scaler, model, X_test: np.ndarray) -> np.ndarray:
    return model.predict_proba(scaler.transform(X_test))[:, 1]


# --- metrics ------------------------------------------------------------------

@dataclass
class Metrics:
    n: int
    n_positive: int
    brier: float
    brier_skill_vs_reference: float
    roc_auc: float | None
    pr_auc: float | None

    def report(self, label: str) -> str:
        auc = "%.3f" % self.roc_auc if self.roc_auc is not None else "n/a (one class)"
        pr = "%.3f" % self.pr_auc if self.pr_auc is not None else "n/a (one class)"
        return ("%-28s n=%-6s positives=%-5s brier=%.4f (skill %+.3f)  "
                "roc_auc=%-16s pr_auc=%s"
                % (label, format(self.n, ","), self.n_positive, self.brier,
                   self.brier_skill_vs_reference, auc, pr))


def evaluate(y_true: np.ndarray, p_pred: np.ndarray, p_reference: np.ndarray
            ) -> Metrics:
    """Brier score, Brier skill score vs. the reference, ROC AUC, PR AUC.

    AUC metrics are undefined with only one class present (can happen in a
    small held-out fold); reported as None rather than a misleading number.
    """
    brier = float(brier_score_loss(y_true, p_pred))
    brier_ref = float(brier_score_loss(y_true, p_reference))
    skill = 1.0 - brier / brier_ref if brier_ref > 0 else float("nan")

    n_classes = len(np.unique(y_true))
    roc_auc = float(roc_auc_score(y_true, p_pred)) if n_classes == 2 else None
    pr_auc = float(average_precision_score(y_true, p_pred)) if n_classes == 2 else None

    return Metrics(n=len(y_true), n_positive=int(y_true.sum()),
                   brier=brier, brier_skill_vs_reference=skill,
                   roc_auc=roc_auc, pr_auc=pr_auc)


# --- leave-one-month-out provisional cross-validation ------------------------

@dataclass
class CVResult:
    model_name: str
    target: str
    months: list[str]
    metrics: Metrics
    n_dropped_nan: int
    per_month_n: dict[str, int] = field(default_factory=dict)


def leave_one_month_out(data: SubsetData, model_name: str, target: str,
                        signal_fraction: float = 0.20) -> CVResult:
    """Out-of-fold predictions, pooled across all held-out months, then scored
    once on the pooled set (per-fold AUC is often undefined on a small,
    imbalanced held-out month).
    """
    months = sorted(set(data.month))
    y_pred = np.full(len(data.y), np.nan)
    y_ref = np.full(len(data.y), np.nan)
    per_month_n = {}

    for held_out in months:
        test_mask = data.month == held_out
        train_mask = ~test_mask
        per_month_n[held_out] = int(test_mask.sum())

        if model_name == "reference":
            y_pred[test_mask] = reference_prediction(data.y[train_mask],
                                                      test_mask.sum())
        elif model_name == "raw_reliability":
            y_pred[test_mask] = raw_reliability_prediction(
                data.member_fraction[test_mask], target, signal_fraction)
        elif model_name == "logistic":
            scaler, model = fit_logistic(data.X[train_mask], data.y[train_mask])
            y_pred[test_mask] = predict_logistic(scaler, model, data.X[test_mask])
        else:
            raise ValueError("unknown model_name: " + model_name)

        y_ref[test_mask] = reference_prediction(data.y[train_mask], test_mask.sum())

    assert not np.isnan(y_pred).any(), "every row must get an out-of-fold prediction"
    metrics = evaluate(data.y, y_pred, y_ref)
    return CVResult(model_name=model_name, target=target, months=months,
                    metrics=metrics, n_dropped_nan=data.n_dropped_nan,
                    per_month_n=per_month_n)
