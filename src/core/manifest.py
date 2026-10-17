"""Dataset manifests and run logging (CLAUDE.md sections 2.11 and 5.5).

A manifest records, for every raw file: source, product and version, the request
parameters used, download date, file name, size and SHA-256. Hashed forecast
records point at a manifest version, so this is part of the audit trail, not
bookkeeping.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, load_config


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path, **extra: Any) -> dict:
    """Standard per-file manifest entry."""
    path = Path(path)
    entry = {
        "file_name": path.name,
        "relative_path": str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/"),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "downloaded_utc": utc_now_iso(),
    }
    entry.update(extra)
    return entry


class Manifest:
    """Append-only manifest for one dataset chunk (e.g. one IMERG month)."""

    def __init__(self, manifest_id: str, source: str, product: str,
                 version: str, request: dict | None = None):
        self.manifest_id = manifest_id
        self.data: dict[str, Any] = {
            "manifest_id": manifest_id,
            "source": source,
            "product": product,
            "version": version,
            "request_parameters": request or {},
            "created_utc": utc_now_iso(),
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "files": [],
            "issues": [],
        }

    def add_file(self, path: Path, **extra: Any) -> dict:
        entry = file_entry(path, **extra)
        self.data["files"].append(entry)
        return entry

    def add_issue(self, kind: str, detail: str, **extra: Any) -> None:
        """Record a gap or anomaly. Never skip a problem silently."""
        self.data["issues"].append(
            {"kind": kind, "detail": detail, "logged_utc": utc_now_iso(), **extra}
        )

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    @property
    def n_files(self) -> int:
        return len(self.data["files"])

    @property
    def total_bytes(self) -> int:
        return sum(f["size_bytes"] for f in self.data["files"])

    def write(self, directory: Path | None = None) -> Path:
        cfg = load_config()
        directory = Path(directory) if directory else cfg.get_path("manifests")
        directory.mkdir(parents=True, exist_ok=True)
        self.data["completed_utc"] = utc_now_iso()
        self.data["summary"] = {
            "n_files": self.n_files,
            "total_bytes": self.total_bytes,
            "total_mb": round(self.total_bytes / 1e6, 2),
            "n_issues": len(self.data["issues"]),
        }
        out = directory / f"{self.manifest_id}.json"
        # Manifests are append-only records: never clobber one silently.
        if out.exists():
            previous = out.with_suffix(f".superseded_{utc_now_iso().replace(':', '')}.json")
            out.rename(previous)
            self.data["supersedes"] = previous.name
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        return out


def load_manifest(manifest_id: str) -> dict:
    cfg = load_config()
    path = cfg.get_path("manifests") / f"{manifest_id}.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def setup_logging(run_name: str, verbose: bool = True) -> logging.Logger:
    """Log a run to both the console and logs/<run_name>_<timestamp>.log."""
    cfg = load_config()
    log_dir = cfg.get_path("logs")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_file = log_dir / f"{run_name}_{stamp}.log"

    logger = logging.getLogger(run_name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            "%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime   # log in UTC, matching the Z suffix

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.info("run started: %s  (log: %s)", run_name, log_file)
    return logger
