"""Verification of a published record against the registry (CLAUDE.md 10.5).

The flow, and what each step actually proves:

1. fetch the public record JSON
2. recompute SHA-256 from its canonical bytes
3. compare with the hash held in the registry   -> the record is unaltered
4. check commit time is before the window start -> it existed beforehand
5. check the outcome, if present                -> the result is also fixed

What this does NOT prove, and the report must say so: that the forecast was
any good, that the model was sensible, or that every forecast the owner made
was submitted. It proves the record existed, unchanged, at the committed time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .record import canonical_bytes, record_hash
from .registry_client import CommitEntry


class Status(str, Enum):
    PENDING = "PENDING"                   # record exists, nothing committed
    COMMITTED = "COMMITTED"               # hash on the registry, no outcome yet
    OUTCOME_RECORDED = "OUTCOME_RECORDED" # outcome present, not yet fully checked
    VERIFIED = "VERIFIED"                 # everything checks out
    MISMATCH = "MISMATCH"                 # the record does not match its hash
    MISSED_COMMIT = "MISSED_COMMIT"       # committed at or after the window opened


@dataclass
class VerificationResult:
    forecast_id: str
    status: Status
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    computed_hash: str | None = None
    registry_hash: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == Status.VERIFIED

    def report(self) -> str:
        lines = ["%s  ->  %s" % (self.forecast_id, self.status.value)]
        for name, passed, detail in self.checks:
            lines.append("  [%s] %-34s %s" % ("PASS" if passed else "FAIL",
                                              name, detail))
        return "\n".join(lines)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def verify_record(record: dict, entry: CommitEntry | None,
                  outcome: dict | None = None) -> VerificationResult:
    """Run every check and report which passed, not just an overall verdict."""
    fid = record.get("forecast_id", "<unknown>")
    checks: list[tuple[str, bool, str]] = []

    computed = record_hash(record)

    if entry is None:
        checks.append(("registry entry exists", False,
                       "no commit found for this id"))
        return VerificationResult(fid, Status.PENDING, checks, computed, None)
    checks.append(("registry entry exists", True,
                   "committed %s" % entry.committed_utc))

    hash_ok = computed == entry.forecast_hash
    checks.append(("record matches committed hash", hash_ok,
                   "computed %s%s" % (computed[:16],
                                      "" if hash_ok else
                                      " != registry %s" % entry.forecast_hash[:16])))
    if not hash_ok:
        return VerificationResult(fid, Status.MISMATCH, checks, computed,
                                  entry.forecast_hash)

    committed_before = _parse(entry.committed_utc) < _parse(entry.window_start_utc)
    checks.append(("committed before window opened", committed_before,
                   "commit %s, window starts %s"
                   % (entry.committed_utc, entry.window_start_utc)))
    if not committed_before:
        return VerificationResult(fid, Status.MISSED_COMMIT, checks, computed,
                                  entry.forecast_hash)

    if entry.outcome_hash is None:
        checks.append(("outcome recorded", False, "window may still be open"))
        return VerificationResult(fid, Status.COMMITTED, checks, computed,
                                  entry.forecast_hash)
    checks.append(("outcome recorded", True,
                   "at %s" % entry.outcome_recorded_utc))

    if outcome is None:
        checks.append(("outcome record available", False,
                       "registry holds a hash but no record was supplied"))
        return VerificationResult(fid, Status.OUTCOME_RECORDED, checks,
                                  computed, entry.forecast_hash)

    outcome_ok = record_hash(outcome) == entry.outcome_hash
    checks.append(("outcome matches committed hash", outcome_ok, ""))
    if not outcome_ok:
        return VerificationResult(fid, Status.MISMATCH, checks, computed,
                                  entry.forecast_hash)

    linked = outcome.get("forecast_hash") == computed
    checks.append(("outcome links to this forecast", linked,
                   "outcome references the forecast hash"))
    if not linked:
        return VerificationResult(fid, Status.MISMATCH, checks, computed,
                                  entry.forecast_hash)

    after_window = _parse(entry.outcome_recorded_utc) >= _parse(entry.window_end_utc)
    checks.append(("outcome recorded after window closed", after_window,
                   "outcome %s, window ends %s"
                   % (entry.outcome_recorded_utc, entry.window_end_utc)))
    if not after_window:
        return VerificationResult(fid, Status.MISMATCH, checks, computed,
                                  entry.forecast_hash)

    return VerificationResult(fid, Status.VERIFIED, checks, computed,
                              entry.forecast_hash)


def verify_from_files(record_path: Path | str, registry, outcome_path=None
                      ) -> VerificationResult:
    """Verify a record stored as JSON on disk against a registry."""
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    outcome = (json.loads(Path(outcome_path).read_text(encoding="utf-8"))
               if outcome_path and Path(outcome_path).exists() else None)
    entry = registry.get(record.get("forecast_id", ""))
    return verify_record(record, entry, outcome)
