"""Probability calibration and trust bands (CLAUDE.md sections 7.1 and 7.3).

A model fitted with class weighting ranks well but is not calibrated: its
output is not a probability you can read as "this decision is wrong 42% of the
time". Calibration fixes that, and it is fitted on the **calibration split
only** - never on train (which would just re-learn the training fit) and never
on test (which would leak).

Two calibrators are provided:
  isotonic  non-parametric, flexible, needs a reasonable number of points
  platt     logistic on the model's score, safer on small calibration sets

Trust bands turn the calibrated p_wrong into HIGH / MEDIUM / LOW using the
cutoffs frozen in configs/project.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .config import Config, load_config


@dataclass
class Calibrator:
    """A fitted calibration map from raw model score to calibrated p_wrong."""
    kind: str
    model: object
    n_fit: int
    n_positive_fit: int

    def apply(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype="float64")
        if self.kind == "isotonic":
            out = self.model.predict(scores)
        elif self.kind == "platt":
            out = self.model.predict_proba(scores.reshape(-1, 1))[:, 1]
        elif self.kind == "identity":
            out = scores
        else:  # pragma: no cover - guarded at fit time
            raise ValueError("unknown calibrator: " + self.kind)
        return np.clip(out, 0.0, 1.0)


def fit_calibrator(scores: np.ndarray, y: np.ndarray, kind: str = "isotonic"
                   ) -> Calibrator:
    """Fit a calibration map on the calibration split only.

    Falls back to the identity map (with the reason recorded in `kind`) when
    the calibration set has only one class, since neither calibrator can be
    fitted in that case and silently returning a constant would be worse.
    """
    scores = np.asarray(scores, dtype="float64")
    y = np.asarray(y, dtype="float64")

    if len(np.unique(y)) < 2:
        return Calibrator(kind="identity", model=None, n_fit=len(y),
                          n_positive_fit=int(y.sum()))

    if kind == "isotonic":
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(scores, y)
    elif kind == "platt":
        model = LogisticRegression(max_iter=2000)
        model.fit(scores.reshape(-1, 1), y)
    else:
        raise ValueError("unknown calibrator kind: " + kind)

    return Calibrator(kind=kind, model=model, n_fit=len(y),
                      n_positive_fit=int(y.sum()))


# --- trust bands -------------------------------------------------------------

def trust_label(p_wrong, cfg: Config | None = None):
    """HIGH / MEDIUM / LOW from calibrated p_wrong, using the frozen cutoffs.

    HIGH   p_wrong <  high_max      (0.20)
    MEDIUM high_max <= p < medium_max (0.20 to 0.50)
    LOW    p_wrong >= medium_max    (0.50)
    """
    cfg = cfg or load_config()
    bands = cfg["trust_bands"]
    high, medium = float(bands["high_max"]), float(bands["medium_max"])

    p = np.asarray(p_wrong, dtype="float64")
    labels = np.full(p.shape, "LOW", dtype=object)
    labels[p < medium] = "MEDIUM"
    labels[p < high] = "HIGH"
    return labels if labels.shape else labels.item()


def band_error_rates(p_wrong: np.ndarray, y_wrong: np.ndarray,
                     cfg: Config | None = None) -> dict:
    """Observed error rate within each trust band - the headline result.

    CLAUDE.md section 8.2: "among forecasts labelled LOW trust, what share of
    errors do they capture? How does the error rate in HIGH versus LOW trust
    bands differ?"
    """
    cfg = cfg or load_config()
    labels = trust_label(p_wrong, cfg)
    y = np.asarray(y_wrong, dtype="float64")
    total_errors = float(y.sum())

    out = {}
    for band in ("HIGH", "MEDIUM", "LOW"):
        mask = labels == band
        n = int(mask.sum())
        errors = float(y[mask].sum()) if n else 0.0
        out[band] = {
            "n": n,
            "share_of_samples": n / len(y) if len(y) else float("nan"),
            "n_errors": int(errors),
            "error_rate": errors / n if n else float("nan"),
            "share_of_all_errors": errors / total_errors if total_errors else float("nan"),
        }
    return out


# --- reliability --------------------------------------------------------------

def reliability_curve(p_pred: np.ndarray, y_true: np.ndarray, n_bins: int = 10
                      ) -> dict:
    """Binned reliability diagram data, plus expected calibration error.

    Returns per-bin mean predicted probability and observed frequency; a
    perfectly calibrated model has them equal in every bin.
    """
    p = np.asarray(p_pred, dtype="float64")
    y = np.asarray(y_true, dtype="float64")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)

    bins = []
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            bins.append({"bin": b, "n": 0, "mean_predicted": None,
                        "observed_frequency": None})
            continue
        mean_p = float(p[mask].mean())
        obs = float(y[mask].mean())
        bins.append({"bin": b, "n": n, "mean_predicted": mean_p,
                    "observed_frequency": obs})
        ece += (n / len(p)) * abs(mean_p - obs)

    return {"bins": bins, "expected_calibration_error": ece, "n_bins": n_bins}
