"""Tests for the audit layer: canonical hashing, the registry rules, and
verification.

CLAUDE.md section 13 requires: "the same record always gives the same hash,
and any field change breaks it", plus contract-rule tests for duplicate id,
empty hash, late commit, early outcome, double outcome.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audit.record import (  # noqa: E402
    ForecastRecord,
    OutcomeRecord,
    canonical_bytes,
    forecast_id,
    record_hash,
    to_basis_points,
    to_hundredths_mm,
)
from src.audit.registry_client import MockRegistry, RegistryError  # noqa: E402
from src.audit.verify import Status, verify_record  # noqa: E402

WINDOW_START = "2022-08-16T00:00:00Z"
WINDOW_END = "2022-08-17T00:00:00Z"


def _record(**overrides) -> ForecastRecord:
    kwargs = dict(
        forecast_id="PK-NCEP-20220815-D2-PK_NORTH",
        region_id="pk_north_30_37_69_75",
        issue_time_utc="2022-08-15T00:00:00Z",
        window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END,
        lead_hours=48,
        signal_bp=3500,
        p_wrong_bp=4200,
        trust_label="MEDIUM",
        threshold_rule="p95_jjas_train_months_per_cell",
        threshold_manifest="thresholds_v1",
        signal_rule="member_fraction_ge_2000bp",
        trust_band_rule="bands_v1",
        model_version="monsoonguard_v0.1",
        input_data_manifest="manifest_v1",
        code_commit="abc123",
    )
    kwargs.update(overrides)
    return ForecastRecord(**kwargs)


def _clock(t: str):
    moment = datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return lambda: moment


# --- unit conversions ---------------------------------------------------------

def test_basis_points_conversion():
    assert to_basis_points(0.78) == 7800
    assert to_basis_points(0.0) == 0
    assert to_basis_points(1.0) == 10000


def test_basis_points_rejects_out_of_range():
    with pytest.raises(ValueError):
        to_basis_points(1.5)
    with pytest.raises(ValueError):
        to_basis_points(-0.1)


def test_hundredths_mm_conversion():
    assert to_hundredths_mm(12.34) == 1234
    with pytest.raises(ValueError):
        to_hundredths_mm(-1.0)


# --- canonical hashing --------------------------------------------------------

def test_same_record_always_gives_the_same_hash():
    """CLAUDE.md section 13."""
    a, b = _record(), _record()
    assert a.hash() == b.hash()
    assert len(a.hash()) == 64


def test_key_order_does_not_change_the_hash():
    """Canonical serialisation sorts keys, so insertion order is irrelevant."""
    d = _record().as_dict()
    reordered = dict(reversed(list(d.items())))
    assert record_hash(d) == record_hash(reordered)


@pytest.mark.parametrize("field,value", [
    ("p_wrong_bp", 4201),
    ("signal_bp", 3501),
    ("trust_label", "LOW"),
    ("lead_hours", 24),
    ("model_version", "monsoonguard_v0.2"),
    ("code_commit", "def456"),
    ("window_start_utc", "2022-08-16T00:00:01Z"),
])
def test_any_field_change_breaks_the_hash(field, value):
    """CLAUDE.md section 13: any field change must break the hash."""
    assert _record().hash() != _record(**{field: value}).hash()


def test_floats_are_rejected_in_hashed_records():
    """Float text form is not stable across platforms, so the hash would not
    reproduce. Probabilities must be basis points."""
    with pytest.raises(TypeError, match="float found"):
        canonical_bytes({"p_wrong": 0.42})


def test_nested_floats_are_also_rejected():
    with pytest.raises(TypeError, match="float found"):
        canonical_bytes({"a": {"b": [1, 2, 3.5]}})


def test_canonical_bytes_have_no_insignificant_whitespace():
    raw = canonical_bytes({"b": 1, "a": 2})
    assert raw == b'{"a":2,"b":1}'


def test_forecast_id_format():
    fid = forecast_id("pk_north", "2022-08-15", 2)
    assert fid == "PK-NCEP-20220815-D2-PK_NORTH"


# --- registry rules (CLAUDE.md section 10.4) ---------------------------------

def test_commit_succeeds_before_the_window_opens(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    entry = reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    assert entry.committed_utc == "2022-08-15T06:00:00Z"


def test_commit_time_comes_from_the_registry_not_the_caller(tmp_path):
    """The caller never supplies the commit time - that is the whole point."""
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    entry = reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    assert entry.committed_utc == "2022-08-15T06:00:00Z"


def test_duplicate_commit_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    with pytest.raises(RegistryError, match="duplicate"):
        reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)


def test_empty_hash_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    with pytest.raises(RegistryError, match="empty hash"):
        reg.commit("id", "0" * 64, WINDOW_START, WINDOW_END)
    with pytest.raises(RegistryError, match="empty hash"):
        reg.commit("id", "", WINDOW_START, WINDOW_END)


def test_late_commit_is_rejected(tmp_path):
    """Committing after the window has opened proves nothing about timing."""
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-16T01:00:00Z"))
    rec = _record()
    with pytest.raises(RegistryError, match="before the window opens"):
        reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)


def test_commit_exactly_at_window_start_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock(WINDOW_START))
    rec = _record()
    with pytest.raises(RegistryError, match="before the window opens"):
        reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)


def test_window_start_must_precede_window_end(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    with pytest.raises(RegistryError, match="windowStart must be before"):
        reg.commit("id", "a" * 64, WINDOW_END, WINDOW_START)


def test_outcome_before_window_closes_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    reg._now = _clock("2022-08-16T12:00:00Z")     # still inside the window
    with pytest.raises(RegistryError, match="before the window closes"):
        reg.record_outcome(rec.forecast_id, "b" * 64)


def test_double_outcome_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    reg._now = _clock("2022-08-18T00:00:00Z")
    reg.record_outcome(rec.forecast_id, "b" * 64)
    with pytest.raises(RegistryError, match="already recorded"):
        reg.record_outcome(rec.forecast_id, "c" * 64)


def test_outcome_for_unknown_forecast_is_rejected(tmp_path):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-18T00:00:00Z"))
    with pytest.raises(RegistryError, match="no such forecast"):
        reg.record_outcome("never-committed", "b" * 64)


def test_registry_persists_across_instances(tmp_path):
    path = tmp_path / "r.json"
    reg = MockRegistry(path, now_fn=_clock("2022-08-15T06:00:00Z"))
    rec = _record()
    reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)

    reopened = MockRegistry(path)
    assert reopened.get(rec.forecast_id).forecast_hash == rec.hash()


# --- verification -------------------------------------------------------------

def _committed_registry(tmp_path, rec):
    reg = MockRegistry(tmp_path / "r.json", now_fn=_clock("2022-08-15T06:00:00Z"))
    reg.commit(rec.forecast_id, rec.hash(), WINDOW_START, WINDOW_END)
    return reg


def test_verification_pending_when_nothing_committed():
    result = verify_record(_record().as_dict(), None)
    assert result.status == Status.PENDING
    assert not result.ok


def test_verification_committed_without_outcome(tmp_path):
    rec = _record()
    reg = _committed_registry(tmp_path, rec)
    result = verify_record(rec.as_dict(), reg.get(rec.forecast_id))
    assert result.status == Status.COMMITTED


def test_verification_detects_a_tampered_record(tmp_path):
    """The point of the whole layer: altering the published record after
    commit must be detectable."""
    rec = _record()
    reg = _committed_registry(tmp_path, rec)

    tampered = rec.as_dict()
    tampered["p_wrong_bp"] = 100          # claim the forecast was far safer
    result = verify_record(tampered, reg.get(rec.forecast_id))

    assert result.status == Status.MISMATCH
    assert not result.ok
    assert any(name == "record matches committed hash" and not passed
               for name, passed, _ in result.checks)


def test_full_verification_passes_end_to_end(tmp_path):
    rec = _record()
    reg = _committed_registry(tmp_path, rec)

    outcome = OutcomeRecord(
        forecast_id=rec.forecast_id, forecast_hash=rec.hash(),
        observation_product="GPM_3IMERGDF", observation_version="07",
        observation_run="Final", window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END, observed_value_hundredths_mm=4512,
        event_occurred=True, decision_was_wrong=False,
        retrieved_utc="2022-08-18T00:00:00Z")

    reg._now = _clock("2022-08-18T00:00:00Z")
    reg.record_outcome(rec.forecast_id, outcome.hash())

    result = verify_record(rec.as_dict(), reg.get(rec.forecast_id),
                           outcome.as_dict())
    assert result.status == Status.VERIFIED
    assert result.ok


def test_verification_detects_a_tampered_outcome(tmp_path):
    rec = _record()
    reg = _committed_registry(tmp_path, rec)
    outcome = OutcomeRecord(
        forecast_id=rec.forecast_id, forecast_hash=rec.hash(),
        observation_product="GPM_3IMERGDF", observation_version="07",
        observation_run="Final", window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END, observed_value_hundredths_mm=4512,
        event_occurred=True, decision_was_wrong=False,
        retrieved_utc="2022-08-18T00:00:00Z")
    reg._now = _clock("2022-08-18T00:00:00Z")
    reg.record_outcome(rec.forecast_id, outcome.hash())

    tampered = outcome.as_dict()
    tampered["event_occurred"] = False
    result = verify_record(rec.as_dict(), reg.get(rec.forecast_id), tampered)
    assert result.status == Status.MISMATCH


def test_verification_detects_an_outcome_linked_to_a_different_forecast(tmp_path):
    rec = _record()
    reg = _committed_registry(tmp_path, rec)
    outcome = OutcomeRecord(
        forecast_id=rec.forecast_id, forecast_hash="f" * 64,   # wrong link
        observation_product="GPM_3IMERGDF", observation_version="07",
        observation_run="Final", window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END, observed_value_hundredths_mm=4512,
        event_occurred=True, decision_was_wrong=False,
        retrieved_utc="2022-08-18T00:00:00Z")
    reg._now = _clock("2022-08-18T00:00:00Z")
    reg.record_outcome(rec.forecast_id, outcome.hash())

    result = verify_record(rec.as_dict(), reg.get(rec.forecast_id),
                           outcome.as_dict())
    assert result.status == Status.MISMATCH
    assert any(name == "outcome links to this forecast" and not passed
               for name, passed, _ in result.checks)


def test_verification_report_lists_every_check(tmp_path):
    rec = _record()
    reg = _committed_registry(tmp_path, rec)
    result = verify_record(rec.as_dict(), reg.get(rec.forecast_id))
    text = result.report()
    assert "record matches committed hash" in text
    assert "committed before window opened" in text
