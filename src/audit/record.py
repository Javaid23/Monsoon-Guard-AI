"""Canonical forecast records and their hashes (CLAUDE.md section 10.2).

A record is committed to the chain as a SHA-256 of its canonical bytes. For
that hash to be reproducible by anyone, the serialisation must be exactly
defined, so this module follows RFC 8785 style canonical JSON: sorted keys,
no insignificant whitespace, UTF-8.

Floating point numbers are forbidden inside a hashed record. Different
languages and platforms print floats differently, which would change the hash
for identical data. Probabilities are integers in basis points (7800 = 78.00%)
and rainfall is in hundredths of a millimetre.

This package may import from core; core must never import from audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"


def to_basis_points(probability: float) -> int:
    """0.78 -> 7800. Rejects anything outside [0, 1]."""
    if not (0.0 <= probability <= 1.0):
        raise ValueError("probability out of range: %r" % (probability,))
    return int(round(probability * 10000))


def to_hundredths_mm(millimetres: float) -> int:
    """12.34 mm -> 1234. Rejects negatives."""
    if millimetres < 0:
        raise ValueError("negative rainfall: %r" % (millimetres,))
    return int(round(millimetres * 100))


def canonical_bytes(record: dict) -> bytes:
    """Deterministic UTF-8 bytes for a record.

    Sorted keys, compact separators, no non-ASCII escaping surprises. Any
    float anywhere in the structure is rejected rather than silently hashed,
    because its text form is not stable across platforms.
    """
    _reject_floats(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _reject_floats(value, path="") -> None:
    if isinstance(value, float):
        raise TypeError(
            "float found at %r: hashed records must use integers "
            "(basis points, hundredths of mm) so the hash is reproducible"
            % (path or "<root>"))
    if isinstance(value, dict):
        for k, v in value.items():
            _reject_floats(v, "%s.%s" % (path, k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _reject_floats(v, "%s[%d]" % (path, i))


def record_hash(record: dict) -> str:
    """SHA-256 hex digest of the canonical bytes."""
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


# --- building records ---------------------------------------------------------

def forecast_id(region_id: str, issue_date: str, lead_day: int,
                source: str = "PK") -> str:
    """Stable identifier, e.g. PK-NCEP-20220815-D2-REGION1."""
    compact = issue_date.replace("-", "")
    return "%s-NCEP-%s-D%d-%s" % (source, compact, lead_day, region_id.upper())


@dataclass
class ForecastRecord:
    """The record that gets hashed and committed before the window opens."""
    forecast_id: str
    region_id: str
    issue_time_utc: str
    window_start_utc: str
    window_end_utc: str
    lead_hours: int
    signal_bp: int
    p_wrong_bp: int
    trust_label: str
    threshold_rule: str
    threshold_manifest: str
    signal_rule: str
    trust_band_rule: str
    model_version: str
    input_data_manifest: str
    code_commit: str
    forecast_source: str = "TIGGE_NCEP_ecds"
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "forecast_id": self.forecast_id,
            "region_id": self.region_id,
            "forecast_source": self.forecast_source,
            "issue_time_utc": self.issue_time_utc,
            "window_start_utc": self.window_start_utc,
            "window_end_utc": self.window_end_utc,
            "lead_hours": self.lead_hours,
            "event_definition": {
                "variable": "daily_precipitation",
                "threshold_rule": self.threshold_rule,
                "threshold_manifest": self.threshold_manifest,
                "signal_rule": self.signal_rule,
            },
            "signal_bp": self.signal_bp,
            "p_wrong_bp": self.p_wrong_bp,
            "trust_label": self.trust_label,
            "trust_band_rule": self.trust_band_rule,
            "model_version": self.model_version,
            "input_data_manifest": self.input_data_manifest,
            "code_commit": self.code_commit,
        }

    def hash(self) -> str:
        return record_hash(self.as_dict())


@dataclass
class OutcomeRecord:
    """Attached after the window closes, linked by forecast_id."""
    forecast_id: str
    forecast_hash: str
    observation_product: str
    observation_version: str
    observation_run: str
    window_start_utc: str
    window_end_utc: str
    observed_value_hundredths_mm: int
    event_occurred: bool
    decision_was_wrong: bool
    retrieved_utc: str
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "forecast_id": self.forecast_id,
            "forecast_hash": self.forecast_hash,
            "observation": {
                "product": self.observation_product,
                "version": self.observation_version,
                "run": self.observation_run,
            },
            "window_start_utc": self.window_start_utc,
            "window_end_utc": self.window_end_utc,
            "observed_value_hundredths_mm": self.observed_value_hundredths_mm,
            "event_occurred": self.event_occurred,
            "decision_was_wrong": self.decision_was_wrong,
            "retrieved_utc": self.retrieved_utc,
        }

    def hash(self) -> str:
        return record_hash(self.as_dict())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
