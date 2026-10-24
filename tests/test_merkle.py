"""Merkle tree tests.

The tree is built in Python and verified in Solidity, so the two
implementations must agree bit for bit. The cases below pin the properties
that keep them in step: the odd-node carry-up, sorted-pair ordering, and the
refusal of a tampered leaf. A cross-language check against the deployed
contract runs in scripts/publish_archive.py before anything is published.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audit.merkle import (  # noqa: E402
    hash_pair, leaf_of, merkle_proof, merkle_root, verify_proof)


def leaves(n: int) -> list[bytes]:
    return [leaf_of(b"record-%d" % i) for i in range(n)]


def test_leaf_is_sha256_of_the_canonical_bytes():
    assert leaf_of(b"x") == hashlib.sha256(b"x").digest()


def test_single_leaf_is_its_own_root():
    only = leaves(1)
    assert merkle_root(only) == only[0]
    assert merkle_proof(only, 0) == []


def test_empty_tree_is_refused():
    with pytest.raises(ValueError):
        merkle_root([])


def test_pair_order_does_not_matter():
    a, b = leaf_of(b"a"), leaf_of(b"b")
    assert hash_pair(a, b) == hash_pair(b, a)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 13, 26, 27])
def test_every_leaf_proves_against_the_root(n):
    """Odd counts matter most: they exercise the carry-up branch."""
    ls = leaves(n)
    root = merkle_root(ls)
    for i, leaf in enumerate(ls):
        assert verify_proof(leaf, merkle_proof(ls, i), root), \
            "leaf %d of %d failed its own proof" % (i, n)


def test_a_tampered_leaf_fails_its_proof():
    ls = leaves(13)
    root = merkle_root(ls)
    proof = merkle_proof(ls, 4)
    assert verify_proof(ls[4], proof, root)
    assert not verify_proof(leaf_of(b"record-4-edited"), proof, root)


def test_a_foreign_leaf_fails():
    ls = leaves(13)
    assert not verify_proof(leaf_of(b"never-happened"),
                            merkle_proof(ls, 0), merkle_root(ls))


def test_changing_any_record_changes_the_root():
    ls = leaves(26)
    before = merkle_root(ls)
    ls[17] = leaf_of(b"record-17-edited")
    assert merkle_root(ls) != before


def test_reordering_leaves_changes_the_root():
    """Leaves are published in sorted forecast-id order so the root is
    reproducible from the public files."""
    ls = leaves(13)
    swapped = list(ls)
    swapped[0], swapped[2] = swapped[2], swapped[0]
    assert merkle_root(swapped) != merkle_root(ls)


def test_sorted_pair_hashing_is_mirror_symmetric():
    """A known and accepted property, recorded here so it is not mistaken
    for a bug later: because each pair is hashed smallest-first, reversing a
    power-of-two tree yields the same unordered pairs at every level and so
    the same root. It costs nothing here - the publisher always sorts by
    forecast id, so the ordering is fixed - but it means the root identifies
    the *set* of records, not a sequence."""
    ls = leaves(8)
    assert merkle_root(list(reversed(ls))) == merkle_root(ls)


def test_proof_index_is_bounds_checked():
    ls = leaves(4)
    for bad in (-1, 4, 99):
        with pytest.raises(IndexError):
            merkle_proof(ls, bad)
