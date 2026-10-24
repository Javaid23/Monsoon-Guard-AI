"""Anchor the archive fingerprint in the Bitcoin blockchain, for free.

Why this as well as the contract
--------------------------------
CLAUDE.md section 10.4 allows OpenTimestamps as a no-contract fallback. It
turns out to be worth having alongside the contract rather than instead of
it, because the two fail in different ways:

  - the EVM registry enforces the rules that matter to the science (nothing
    sealed after its window opens, no outcome before the window closes) but
    it lives on a chain we chose and pay for;
  - OpenTimestamps enforces nothing, but anchors a hash into Bitcoin itself
    at no cost, with no wallet, no account and no key to lose.

So the registry carries the logic and Bitcoin carries the durability. Both
are checkable by a stranger with no help from us.

How it works: the client sends the file's SHA-256 to public calendar
servers, which batch thousands of hashes into one Bitcoin transaction. That
is why it is free - the cost is shared across everyone stamping at the same
moment. The proof is `records/archive_manifest.json.ots`.

Confirmation is not instant. Immediately after stamping the proof says
"pending"; once the batch lands in a block, `--upgrade` rewrites the proof
so it stands alone and needs no calendar server ever again.

Usage
-----
    python scripts/timestamp_archive.py              # stamp (once)
    python scripts/timestamp_archive.py --upgrade    # after ~1-3 hours
    python scripts/timestamp_archive.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGET = ROOT / "records" / "archive_manifest.json"
PROOF = Path(str(TARGET) + ".ots")

logger = logging.getLogger("timestamp_archive")


def ots_binary() -> str:
    """The client ships as a console script next to the interpreter."""
    found = shutil.which("ots") or shutil.which(
        "ots", path=str(Path(sys.executable).parent))
    if not found:
        raise SystemExit(
            "opentimestamps-client is not installed.\n"
            "    pip install opentimestamps-client")
    return found


def run(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([ots_binary(), *args], cwd=ROOT,
                          capture_output=True, text=True)
    # The client writes its progress to stderr even when it succeeds, so
    # both streams are shown rather than only the one that looks like an error.
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc


ANCHOR = ROOT / "records" / "bitcoin_anchor.json"


def record_anchor() -> dict | None:
    """Turn a confirmed proof into something the app can link to.

    Until Bitcoin includes the batch, the proof names only calendar servers
    and there is nothing to link to. Once it is confirmed it names a block
    height, which is the durable, checkable fact - so that is what gets
    written out, together with the explorer URL and the plain warning that
    the block shows an aggregated root rather than our hash.
    """
    proc = subprocess.run([ots_binary(), "info", str(PROOF)],
                          cwd=ROOT, capture_output=True, text=True)
    heights = re.findall(r"BitcoinBlockHeaderAttestation\((\d+)\)", proc.stdout)
    if not heights:
        if ANCHOR.exists():
            ANCHOR.unlink()      # never leave a stale "confirmed" behind
        return None

    height = min(int(h) for h in heights)   # the earliest block is the claim
    anchor = {
        "status": "confirmed",
        "chain": "bitcoin",
        "block_height": height,
        "file": TARGET.name,
        "file_sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(),
        "proof_file": PROOF.name,
        "explorer_url": "https://mempool.space/block/%d" % height,
        "verify_url": "https://opentimestamps.org",
        "note": ("The block holds an aggregated Merkle root covering many "
                 "unrelated timestamps, not this file's hash in readable "
                 "form. The proof file supplies the path from one to the "
                 "other; verification needs both."),
    }
    ANCHOR.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return anchor


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upgrade", action="store_true",
                    help="bake the proof once Bitcoin has confirmed it")
    ap.add_argument("--verify", action="store_true",
                    help="check the proof and report the block time")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not TARGET.exists():
        raise SystemExit("no archive manifest; run publish_archive.py first")

    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    manifest = json.loads(TARGET.read_text(encoding="utf-8"))
    logger.info("manifest:  %s", TARGET.name)
    logger.info("sha256:    %s", digest)
    logger.info("root:      %s", manifest["merkle_root"])

    if args.upgrade:
        run("upgrade", str(PROOF))
        anchor = record_anchor()
        if anchor:
            logger.info("\nCONFIRMED in Bitcoin block %d", anchor["block_height"])
            logger.info("explorer:  %s", anchor["explorer_url"])
            logger.info("wrote:     %s", ANCHOR.name)
        else:
            logger.info("\nStill pending. Try again in an hour or two.")
    elif args.verify:
        run("verify", str(PROOF))
        record_anchor()
    else:
        if PROOF.exists():
            # Stamping twice would discard the earlier, older proof - and the
            # older one is the more valuable, because it proves an earlier date.
            raise SystemExit(
                "%s already exists. Delete it only if you mean to give up the "
                "earlier timestamp; otherwise use --upgrade or --verify."
                % PROOF.name)
        run("stamp", str(TARGET))
        logger.info("\nStamped. Bitcoin confirmation takes a few hours; run "
                    "with --upgrade afterwards to finalise the proof.")


if __name__ == "__main__":
    main()
