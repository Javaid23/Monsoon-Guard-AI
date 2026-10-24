"""Registry clients: a local mock, and the interface a chain client implements.

CLAUDE.md section 10.1: the chain is optional for the science. If it is
unavailable, records queue as PENDING and are never shown as verified. The
mock implements the same interface so the whole audit layer, including its
tests, runs with no network and no deployed contract.

The rules enforced here mirror the contract requirements in section 10.4:
  commit        rejects duplicates, empty hashes, windowStart >= windowEnd,
                and any commit at or after the window has already started
  recordOutcome requires an existing forecast, no earlier outcome, and
                block time at or after windowEnd
The commit time is always taken from the registry's clock, never supplied by
the caller - that is the whole point of the timestamp.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class RegistryError(RuntimeError):
    """A rule in section 10.4 was violated."""


@dataclass
class CommitEntry:
    forecast_id: str
    forecast_hash: str
    window_start_utc: str
    window_end_utc: str
    committed_utc: str
    outcome_hash: str | None = None
    outcome_recorded_utc: str | None = None


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class MockRegistry:
    """File-backed registry with the same semantics as the contract.

    `now_fn` exists so tests can control the clock; production uses real UTC.
    """

    def __init__(self, path: Path | str | None = None, now_fn=None):
        self.path = Path(path) if path else None
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self.entries: dict[str, CommitEntry] = {}
        if self.path and self.path.exists():
            self._load()

    # --- persistence ---------------------------------------------------------

    def _load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.entries = {k: CommitEntry(**v) for k, v in raw.items()}

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({k: asdict(v) for k, v in self.entries.items()},
                       indent=2, sort_keys=True),
            encoding="utf-8")

    def now_iso(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- the contract surface -------------------------------------------------

    def commit(self, forecast_id: str, forecast_hash: str,
               window_start_utc: str, window_end_utc: str) -> CommitEntry:
        if not forecast_id:
            raise RegistryError("empty forecast id")
        if not forecast_hash or set(forecast_hash) == {"0"}:
            raise RegistryError("empty hash")
        if len(forecast_hash) != 64:
            raise RegistryError("hash must be 64 hex characters (SHA-256)")
        if forecast_id in self.entries:
            raise RegistryError("duplicate forecast id: " + forecast_id)

        start, end = _parse(window_start_utc), _parse(window_end_utc)
        if not start < end:
            raise RegistryError("windowStart must be before windowEnd")

        now = self._now()
        if not now < start:
            raise RegistryError(
                "commit must happen before the window opens (now %s, window "
                "starts %s) - a forecast committed after its window has begun "
                "proves nothing about timing"
                % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), window_start_utc))

        entry = CommitEntry(
            forecast_id=forecast_id, forecast_hash=forecast_hash,
            window_start_utc=window_start_utc, window_end_utc=window_end_utc,
            committed_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.entries[forecast_id] = entry
        self._save()
        return entry

    def record_outcome(self, forecast_id: str, outcome_hash: str) -> CommitEntry:
        entry = self.entries.get(forecast_id)
        if entry is None:
            raise RegistryError("no such forecast: " + forecast_id)
        if entry.outcome_hash is not None:
            raise RegistryError("outcome already recorded for " + forecast_id)
        if not outcome_hash or len(outcome_hash) != 64:
            raise RegistryError("outcome hash must be 64 hex characters")

        now = self._now()
        if now < _parse(entry.window_end_utc):
            raise RegistryError(
                "outcome cannot be recorded before the window closes "
                "(now %s, window ends %s)"
                % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), entry.window_end_utc))

        entry.outcome_hash = outcome_hash
        entry.outcome_recorded_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._save()
        return entry

    def get(self, forecast_id: str) -> CommitEntry | None:
        return self.entries.get(forecast_id)

    def list_ids(self) -> list[str]:
        return sorted(self.entries)
