"""Build - and optionally publish - a Merkle root over the finished records.

Why not just commit them
------------------------
`ForecastRegistry.commit` refuses any record whose rain window has already
opened, and it is right to: a timestamp written in 2026 cannot prove that a
forecast for August 2022 came before the rain. The 2022 set is a replay, and
pushing it through `commit` would dress a retrospective result up as a
prediction.

`publishArchive` makes the claim that *is* true of a replay: this exact set
of results existed, unaltered, at this block timestamp. From publication
onwards nobody - including us - can edit a number without the root changing.
That is worth proving at submission time, and it costs one transaction for
the whole set rather than one per record.

Usage
-----
    python scripts/publish_archive.py                      # compute only
    python scripts/publish_archive.py --check              # + verify in Solidity
    python scripts/publish_archive.py --publish --network sepolia

Nothing is sent to a chain unless --publish is given.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audit import merkle, record as rec_mod  # noqa: E402
from src.core.config import load_config  # noqa: E402

logger = logging.getLogger("publish_archive")


def collect(records_dir: Path) -> list[dict]:
    """Every published record, forecasts and outcomes alike, in id order.

    The leaf is the SHA-256 of the canonical bytes - the same digest the
    verification page recomputes - not of the pretty-printed file on disk,
    so re-indenting a file cannot change the root while editing a value
    still does.
    """
    entries = []
    for path in sorted(records_dir.glob("*.json")):
        if path.name in {"mock_registry.json", "registry_deployment.json",
                         "archive_manifest.json"}:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        if "forecast_id" not in doc:
            logger.warning("%s has no forecast_id, skipped", path.name)
            continue
        entries.append({
            "file": path.name,
            "forecast_id": doc["forecast_id"],
            "kind": "outcome" if path.name.endswith(".outcome.json")
                    else "forecast",
            "sha256": rec_mod.record_hash(doc),
        })
    entries.sort(key=lambda e: (e["forecast_id"], e["kind"]))
    return entries


def build(entries: list[dict]) -> tuple[bytes, list[bytes]]:
    leaves = [bytes.fromhex(e["sha256"]) for e in entries]
    return merkle.merkle_root(leaves), leaves


def solidity_crosscheck(root: bytes, leaves: list[bytes], entries: list[dict],
                        network: str) -> None:
    """Ask the contract itself whether our proofs hold.

    Two implementations of the same tree in two languages is exactly the
    place a silent mismatch hides, and a mismatch found after publishing is
    found too late. This runs every leaf through the real contract.
    """
    payload = {
        "root": "0x" + root.hex(),
        "leaves": ["0x" + l.hex() for l in leaves],
        "proofs": [["0x" + s.hex() for s in merkle.merkle_proof(leaves, i)]
                   for i in range(len(leaves))],
        "label": "jjas-2022-replay",
    }
    tmp = ROOT / "contracts" / ".cache" / "archive_crosscheck.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload), encoding="utf-8")

    cmd = ["npx", "hardhat", "run", "contracts/scripts/check_archive.js"]
    if network:
        cmd += ["--network", network]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          shell=(sys.platform == "win32"))
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("Solidity cross-check failed; nothing published")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify every proof against the compiled contract")
    ap.add_argument("--publish", action="store_true",
                    help="send the publishArchive transaction")
    ap.add_argument("--network", default="",
                    help="hardhat network name, e.g. sepolia")
    ap.add_argument("--label", default="jjas-2022-replay")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    records_dir = cfg.get_path("records")

    entries = collect(records_dir)
    if not entries:
        raise SystemExit("no records found; run build_records.py first")
    root, leaves = build(entries)

    n_fc = sum(1 for e in entries if e["kind"] == "forecast")
    logger.info("records:   %d (%d forecasts, %d outcomes)",
                len(entries), n_fc, len(entries) - n_fc)
    logger.info("root:      0x%s", root.hex())
    logger.info("label:     %s", args.label)

    manifest = {
        "label": args.label,
        "merkle_root": "0x" + root.hex(),
        "leaf_count": len(leaves),
        "leaf_rule": "sha256 of canonical record bytes, sorted by forecast_id",
        "node_rule": "keccak256 over the pair sorted as big-endian integers",
        "records": [
            dict(e, proof=["0x" + s.hex() for s in merkle.merkle_proof(leaves, i)])
            for i, e in enumerate(entries)
        ],
    }
    out = records_dir / "archive_manifest.json"
    body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    # A Bitcoin timestamp covers the exact bytes of this file. Rewriting it
    # with different content silently voids that proof - and a void proof is
    # worse than none, because it still looks like evidence until someone
    # checks it. So refuse, and make giving up the old timestamp a decision
    # rather than an accident.
    proof = records_dir / "archive_manifest.json.ots"
    if proof.exists() and out.exists() and out.read_text(encoding="utf-8") != body:
        raise SystemExit(
            "The manifest has changed, but %s timestamps the old one, so that "
            "proof would no longer verify.\n"
            "The underlying records must have changed. Either restore them, or "
            "delete the proof and re-stamp - which gives up the earlier and "
            "therefore stronger date." % proof.name)

    out.write_text(body, encoding="utf-8")
    logger.info("wrote:     %s", out.name)

    if args.check or args.publish:
        solidity_crosscheck(root, leaves, entries, args.network)

    if args.publish:
        logger.info("publishing is done by contracts/scripts/publish_archive.js")


if __name__ == "__main__":
    main()
