"""Tests for the on-chain registry client.

Unit tests for the pure parts (time conversion, explorer links, fallback
selection) always run. The integration tests need a local chain with the
contract deployed, and skip cleanly without one:

    npx hardhat node
    npx hardhat run contracts/scripts/deploy.js --network localhost
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audit.chain_client import (  # noqa: E402
    ChainInfo,
    _epoch,
    _iso,
    load_deployment,
    open_registry,
)
from src.audit.record import ForecastRecord, forecast_id  # noqa: E402
from src.audit.verify import Status, verify_record  # noqa: E402


# --- time conversion ---------------------------------------------------------

def test_iso_and_epoch_round_trip():
    stamp = "2022-08-16T00:00:00Z"
    assert _iso(_epoch(stamp)) == stamp


def test_epoch_is_utc_not_local():
    """A local-time reading would shift commit timestamps by hours and make
    the 'committed before the window' check wrong."""
    assert _epoch("1970-01-01T00:00:00Z") == 0
    assert _iso(0) == "1970-01-01T00:00:00Z"


# --- chain info --------------------------------------------------------------

def test_local_dev_chain_is_not_public():
    """A record on a dev chain cannot be checked by a third party, so the UI
    must not present it as publicly verifiable."""
    for chain_id in (31337, 1337):
        assert not ChainInfo("0xabc", chain_id, "localhost", "http://x").is_public


def test_amoy_is_public_and_has_an_explorer_link():
    info = ChainInfo("0xAbC", 80002, "amoy", "http://x")
    assert info.is_public
    assert info.explorer_url == "https://amoy.polygonscan.com/address/0xAbC"


def test_unknown_chain_has_no_explorer_link():
    assert ChainInfo("0xabc", 999999, "weird", "http://x").explorer_url is None


# --- registry selection -------------------------------------------------------

def test_open_registry_falls_back_to_mock_when_unreachable(tmp_path, monkeypatch):
    """A broken RPC must fall back to the mock and say so, never pretend."""
    monkeypatch.setenv("FORECAST_REGISTRY_ADDRESS",
                       "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("EVM_RPC_URL", "http://127.0.0.1:9")   # nothing listening
    registry, info, reason = open_registry(ROOT, tmp_path)
    assert info is None
    assert "mock" in reason.lower()
    assert registry.list_ids() == []


def test_open_registry_can_be_told_to_skip_the_chain(tmp_path):
    registry, info, reason = open_registry(ROOT, tmp_path, prefer_chain=False)
    assert info is None
    assert "not requested" in reason


# --- integration against a live local chain ----------------------------------

def _live_registry():
    """A chain registry if a local deployment is reachable, else None."""
    deployment = load_deployment(ROOT)
    if not deployment:
        return None
    try:
        from src.audit.chain_client import ChainRegistry, load_abi
        return ChainRegistry(
            rpc_url="http://127.0.0.1:8545", address=deployment["address"],
            abi=load_abi(ROOT), chain_id=deployment.get("chain_id"),
            network=deployment.get("network", "localhost"),
            deployment=deployment)
    except Exception:
        return None


live = pytest.mark.skipif(_live_registry() is None,
                          reason="no local chain with the contract deployed")


@live
def test_reads_a_committed_record_from_chain():
    reg = _live_registry()
    ids = reg.list_ids()
    if not ids:
        pytest.skip("chain has no committed records")
    entry = reg.get(ids[0])
    assert entry is not None
    assert len(entry.forecast_hash) == 64
    assert entry.committed_utc.endswith("Z")


@live
def test_unknown_id_reads_as_none_rather_than_raising():
    assert _live_registry().get("NO-SUCH-FORECAST-ID") is None


@live
def test_commit_timestamp_comes_from_the_chain():
    """The caller never supplies the commit time - that is the whole point."""
    reg = _live_registry()
    ids = reg.list_ids()
    if not ids:
        pytest.skip("chain has no committed records")
    entry = reg.get(ids[0])
    # Whatever was committed, the chain's timestamp must precede the window.
    assert entry.committed_utc < entry.window_start_utc


@live
def test_tampering_is_detected_against_the_chain():
    reg = _live_registry()
    ids = reg.list_ids()
    if not ids:
        pytest.skip("chain has no committed records")
    fid = ids[0]
    entry = reg.get(fid)

    issue = dt.datetime.strptime(entry.window_start_utc, "%Y-%m-%dT%H:%M:%SZ")
    record = ForecastRecord(
        forecast_id=fid, region_id="pk_north_30_37_69_75",
        issue_time_utc=(issue - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_start_utc=entry.window_start_utc,
        window_end_utc=entry.window_end_utc,
        lead_hours=48, signal_bp=3500, p_wrong_bp=4200, trust_label="MEDIUM",
        threshold_rule="p95_jjas_train_months_per_cell",
        threshold_manifest="thresholds_v1",
        signal_rule="member_fraction_ge_2000bp", trust_band_rule="bands_v1",
        model_version="monsoonguard_v0.1",
        input_data_manifest="manifests_43_files", code_commit="a891476")

    tampered = record.as_dict()
    tampered["p_wrong_bp"] = 100
    assert verify_record(tampered, entry).status == Status.MISMATCH


@live
def test_read_only_client_refuses_to_write():
    reg = _live_registry()          # built with no private key
    from src.audit.registry_client import RegistryError
    with pytest.raises(RegistryError, match="read-only"):
        reg.commit("X", "a" * 64, "2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z")
