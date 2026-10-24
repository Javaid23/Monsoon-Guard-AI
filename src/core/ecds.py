"""Non-blocking client for the ECMWF Data Store (ECDS) OGC Processes API.

Why this exists
---------------
TIGGE is served from ECMWF's tape archive, so a request's cost is queue and
tape time, not bytes: observed server-side waits for one month of NCEP
precipitation (about 1.5 MB) range from 7 minutes to 18 h 44 min. ECDS also
progresses only one job at a time per account.

The `cdsapi` client couples submit, wait and download into a single blocking
call. That has three consequences, all of which cost us real data:

  1. A multi-hour wait must be held open on the network. If the link drops,
     the wait is forfeited.
  2. A forfeited wait strands the job server-side, where it keeps occupying
     the account's single slot and blocks everything submitted after it.
  3. Job results expire. A job that completed while nothing was listening is
     unrecoverable, and the tape wait has to be paid again.

This module separates the three steps so that submissions are durable:
requests are fired once, their job ids are persisted to disk, and collection
can be interrupted and resumed as often as needed. Submitting every month up
front also lets the queue drain while nothing is connected, which turns total
time from the sum of the waits into roughly the longest one.

Endpoints
---------
ECDS exposes both a legacy `/tasks/{id}` namespace and the OGC Processes API.
They do not share job ids: an OGC jobID returns 404 from `/tasks/{id}`. This
module uses the OGC API throughout, which is also where jobs submitted by
`cdsapi` show up, so jobs stranded by an earlier blocking run can be adopted
here by id instead of being resubmitted.

Credentials come from ECDS_URL and ECDS_PAT in .env (CLAUDE.md section 2.8).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.core.config import get_credential, load_dotenv_if_present
from src.core.manifest import utc_now_iso

DEFAULT_URL = "https://ecds.ecmwf.int/api"

# OGC job states. ECDS reports "successful"; the rest are terminal failures
# except for the two live states.
LIVE_STATES = ("accepted", "running")
DONE_STATE = "successful"
DEAD_STATES = ("failed", "dismissed")

# ECDS caps how many requests one account may have in flight. Over the cap the
# API still returns 201 with a jobID and status "accepted", then flips the job
# to "rejected" within about 30 seconds and gives no reason in the job record.
# Observed on 2026-10-24: 4 jobs already queued, 9 more submitted in one burst,
# all 9 rejected within 30 s while the original 4 ran to completion. Identical
# requests had succeeded when submitted a few at a time, so the cause is the
# quota rather than the request.
#
# A rejected job is therefore not a failure of the request and must be
# resubmitted once a slot frees, not abandoned.
RETRY_STATES = ("rejected",)
DEFAULT_MAX_IN_FLIGHT = 4


class EcdsError(RuntimeError):
    pass


# --- client -----------------------------------------------------------------

@dataclass
class EcdsClient:
    """Thin wrapper over the ECDS OGC Processes API. No blocking waits."""

    url: str = ""
    pat: str = ""
    timeout: int = 120

    def __post_init__(self) -> None:
        load_dotenv_if_present()
        self.url = (self.url or get_credential("ECDS_URL") or DEFAULT_URL).rstrip("/")
        self.pat = self.pat or get_credential("ECDS_PAT") or ""
        if not self.pat:
            raise EcdsError(
                "No ECDS token found.\n"
                "Set in .env:\n"
                "  ECDS_URL=https://ecds.ecmwf.int/api\n"
                "  ECDS_PAT=<personal access token from "
                "https://ecds.ecmwf.int/how-to-api>"
            )
        self._session = requests.Session()
        self._session.headers.update({"PRIVATE-TOKEN": self.pat})

        # This machine's DNS drops intermittently: a lookup of ecds.ecmwf.int
        # failed mid-run on 2026-10-24 and killed a collector that had been
        # polling for 40 minutes. Since the whole point of persisting job ids
        # is to ride out a bad network, transport errors must be retried here
        # rather than raised at the caller.
        retry = Retry(
            total=6,
            connect=6,          # covers DNS resolution failures
            read=4,
            backoff_factor=2.0,  # 0s, 4s, 8s, 16s, 32s, 64s
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # -- raw helpers ---------------------------------------------------------

    def _get(self, path: str, **kw: Any) -> requests.Response:
        return self._session.get(self.url + path, timeout=self.timeout, **kw)

    def licence_accepted(self) -> bool | None:
        """True / False, or None if the check itself could not be made."""
        try:
            resp = self._get("/profiles/v1/account/licences")
            accepted = {lic["id"] for lic in resp.json().get("licences", [])}
        except Exception:
            return None
        return "tigge-licence" in accepted

    # -- job lifecycle -------------------------------------------------------

    def submit(self, dataset: str, request: dict) -> str:
        """Queue one request and return its job id. Returns immediately."""
        resp = self._session.post(
            "%s/retrieve/v1/processes/%s/execute" % (self.url, dataset),
            json={"inputs": request},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        if resp.status_code >= 300:
            raise EcdsError("submit failed (HTTP %d): %s"
                            % (resp.status_code, resp.text[:500]))
        job_id = resp.json().get("jobID")
        if not job_id:
            raise EcdsError("submit returned no jobID: %s" % resp.text[:500])
        return str(job_id)

    def status(self, job_id: str) -> str:
        """Current state of one job.

        A job whose record has been purged reports "expired" rather than
        raising, so a stale job store stays readable.
        """
        resp = self._get("/retrieve/v1/jobs/%s" % job_id)
        if resp.status_code == 404:
            return "expired"
        if resp.status_code >= 300:
            raise EcdsError("status failed (HTTP %d): %s"
                            % (resp.status_code, resp.text[:300]))
        return str(resp.json().get("status", "unknown"))

    def states(self, job_ids: Iterable[str]) -> dict[str, str]:
        """States for many jobs, in one listing call where possible.

        The /jobs listing covers everything still on record; anything absent
        from it has been purged and is reported as expired.
        """
        wanted = list(job_ids)
        found: dict[str, str] = {}
        try:
            resp = self._get("/retrieve/v1/jobs",
                             params={"limit": 100, "sortby": "-created"})
            if resp.status_code < 300:
                for job in resp.json().get("jobs", []):
                    found[str(job.get("jobID"))] = str(job.get("status"))
        except Exception:
            pass

        # Fall back to a per-job call only for ids the listing did not cover.
        # A job whose state cannot be established right now is omitted rather
        # than guessed: the caller keeps the state it already had and tries
        # again next pass. Reporting an unknown state as a real one would risk
        # treating a live job as dead and requeuing it, paying the tape wait
        # twice.
        out: dict[str, str] = {}
        for jid in wanted:
            if jid in found:
                out[jid] = found[jid]
                continue
            try:
                out[jid] = self.status(jid)
            except Exception:
                continue
        return out

    def result_href(self, job_id: str) -> tuple[str, int | None]:
        """Download URL and byte size for a successful job."""
        resp = self._get("/retrieve/v1/jobs/%s/results" % job_id)
        if resp.status_code >= 300:
            raise EcdsError("results not available (HTTP %d): %s"
                            % (resp.status_code, resp.text[:300]))
        asset = (resp.json().get("asset") or {}).get("value") or {}
        href = asset.get("href")
        if not href:
            raise EcdsError("results carried no href: %s" % resp.text[:300])
        size = asset.get("file:size")
        return str(href), int(size) if size is not None else None

    def download(self, href: str, target: Path, expect_bytes: int | None = None
                 ) -> int:
        """Stream a result to `target` via a .part file.

        Writing through a temporary name means an interrupted transfer can
        never leave a short file that later looks complete, which is what
        data/raw being append-only depends on (CLAUDE.md section 2.3).
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")

        with self._session.get(href, stream=True, timeout=self.timeout) as resp:
            if resp.status_code >= 300:
                raise EcdsError("download failed (HTTP %d)" % resp.status_code)
            written = 0
            with open(partial, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        written += fh.write(chunk)

        if written == 0:
            partial.unlink(missing_ok=True)
            raise EcdsError("download produced an empty file")
        if expect_bytes is not None and written != expect_bytes:
            partial.unlink(missing_ok=True)
            raise EcdsError("download truncated: got %d bytes, expected %d"
                            % (written, expect_bytes))

        partial.replace(target)
        return written

    def dismiss(self, job_id: str) -> bool:
        """Cancel a queued or running job so it stops holding the slot."""
        try:
            resp = self._session.delete(
                "%s/retrieve/v1/jobs/%s" % (self.url, job_id),
                timeout=self.timeout)
            return resp.status_code < 300
        except Exception:
            return False


# --- durable job store ------------------------------------------------------

@dataclass
class JobStore:
    """Submitted jobs, keyed by the target file each one fills.

    Persisted as JSON so that submit and collect can run as separate
    processes, hours apart, across interruptions. The store is the record of
    what has been asked of ECDS; the GRIB files are the record of what
    arrived.
    """

    path: Path
    jobs: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "JobStore":
        path = Path(path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path=path, jobs=data.get("jobs", {}))
        return cls(path=path, jobs={})

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated_utc": utc_now_iso(), "jobs": self.jobs}
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return self.path

    def add(self, target_name: str, job_id: str, dataset: str, request: dict,
            *, adopted: bool = False, note: str = "") -> dict:
        entry = {
            "job_id": job_id,
            "dataset": dataset,
            "request": request,
            "submitted_utc": utc_now_iso(),
            "status": "accepted",
            "adopted": adopted,
            # An adopted job's request parameters are inferred from submission
            # logs rather than read back from the API, which does not expose
            # them. The downloaded file's own contents decide the truth, so
            # these entries stay flagged until verified.
            "request_verified": not adopted,
            "note": note,
            "attempts": 1,
            "downloaded_utc": None,
        }
        self.jobs[target_name] = entry
        return entry

    def get(self, target_name: str) -> dict | None:
        return self.jobs.get(target_name)

    def live(self) -> dict[str, dict]:
        return {k: v for k, v in self.jobs.items()
                if v.get("status") in LIVE_STATES}

    def pending_download(self) -> dict[str, dict]:
        return {k: v for k, v in self.jobs.items()
                if v.get("status") == DONE_STATE and not v.get("downloaded_utc")}

    def needs_resubmit(self) -> dict[str, dict]:
        """Jobs that must be queued again to ever produce a file.

        Includes quota rejections, which are not request failures: they only
        mean the account had too many jobs in flight at the time.
        """
        return {k: v for k, v in self.jobs.items()
                if v.get("status") in DEAD_STATES + RETRY_STATES
                + ("expired", "download_failed")}

    def resubmit(self, target_name: str, job_id: str) -> dict:
        """Point an existing entry at a freshly queued job."""
        entry = self.jobs[target_name]
        entry.setdefault("previous_job_ids", []).append(entry["job_id"])
        entry["job_id"] = job_id
        entry["status"] = "accepted"
        entry["submitted_utc"] = utc_now_iso()
        entry["attempts"] = int(entry.get("attempts", 1)) + 1
        entry.setdefault("history", []).append(
            {"state": "resubmitted", "at": entry["submitted_utc"]})
        return entry

    def complete(self) -> dict[str, dict]:
        return {k: v for k, v in self.jobs.items()
                if v.get("status") == "complete"}

    def n_in_flight(self) -> int:
        return len(self.live())


def wait_states(client: EcdsClient, store: JobStore, logger,
                interval: int = 60) -> dict[str, str]:
    """Refresh every live job's state once and persist the result."""
    live = store.live()
    if not live:
        return {}
    ids = [e["job_id"] for e in live.values()]
    states = client.states(ids)
    changed = {}
    for name, entry in live.items():
        new = states.get(entry["job_id"], entry["status"])
        if new != entry["status"]:
            entry["status"] = new
            entry.setdefault("history", []).append(
                {"state": new, "at": utc_now_iso()})
            changed[name] = new
            logger.info("%s: %s", name, new)
    if changed:
        store.save()
    return changed


def sleep_between_polls(interval: int) -> None:
    time.sleep(interval)
