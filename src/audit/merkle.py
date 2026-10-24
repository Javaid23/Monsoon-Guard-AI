"""Merkle tree over record hashes, for publishing a finished result set.

Why this exists
---------------
A retrospective result cannot go through `ForecastRegistry.commit`: its rain
window closed years ago, and the contract refuses it on purpose, because no
timestamp written today could prove the forecast came first. But there is
still a true and useful claim to make about those results - that this exact
set existed, unaltered, at a given moment - and `publishArchive` makes it.

One root covers every record, so the whole 2022 replay is a single
transaction instead of twenty-six. Anyone can still check one record on its
own by recomputing its SHA-256 and walking the proof.

Construction (must match ForecastRegistry.verifyArchiveProof exactly):
  - leaves are the SHA-256 digests of the canonical record bytes, in sorted
    forecast-id order, so the root is reproducible from the public files;
  - internal nodes are keccak256 over the two children sorted as integers,
    so a proof needs no position bits;
  - an odd node at any level is carried up unchanged rather than paired with
    itself, which is what closes the duplicate-leaf forgery in the naive
    scheme.

Leaves are SHA-256 while internal nodes are keccak256. That difference is not
cosmetic: it means no leaf can ever be mistaken for an internal node, which
is the second-preimage defence.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Sequence


def _keccak(data: bytes) -> bytes:
    # eth_utils ships keccak; fall back to pysha3-style hashlib if present.
    from eth_utils import keccak as _k
    return _k(data)


def hash_pair(a: bytes, b: bytes) -> bytes:
    """Hash two nodes, smaller first, exactly as the contract does."""
    lo, hi = (a, b) if int.from_bytes(a, "big") <= int.from_bytes(b, "big") else (b, a)
    return _keccak(lo + hi)


def leaf_of(canonical_bytes: bytes) -> bytes:
    """The leaf for one record: SHA-256 of its canonical JSON bytes."""
    return hashlib.sha256(canonical_bytes).digest()


def _levels(leaves: Sequence[bytes]) -> list[list[bytes]]:
    if not leaves:
        raise ValueError("a Merkle tree needs at least one leaf")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt: list[bytes] = []
        for i in range(0, len(cur), 2):
            if i + 1 == len(cur):
                nxt.append(cur[i])          # odd node carries up unchanged
            else:
                nxt.append(hash_pair(cur[i], cur[i + 1]))
        levels.append(nxt)
    return levels


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    return _levels(leaves)[-1][0]


def merkle_proof(leaves: Sequence[bytes], index: int) -> list[bytes]:
    """Sibling hashes from the bottom up for `leaves[index]`."""
    if not 0 <= index < len(leaves):
        raise IndexError("leaf %d out of range for %d leaves" % (index, len(leaves)))
    proof: list[bytes] = []
    levels = _levels(leaves)
    i = index
    for level in levels[:-1]:
        sibling = i ^ 1
        if sibling < len(level):            # no sibling means the node carried up
            proof.append(level[sibling])
        i //= 2
    return proof


def verify_proof(leaf: bytes, proof: Iterable[bytes], root: bytes) -> bool:
    node = leaf
    for sibling in proof:
        node = hash_pair(node, sibling)
    return node == root
