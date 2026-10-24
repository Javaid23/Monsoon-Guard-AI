"""Read and write the deployed ForecastRegistry over JSON-RPC.

This is the chain half of CLAUDE.md section 4's "chain client plus local mock
registry". It presents the same surface as `MockRegistry` — `commit`,
`record_outcome`, `get`, `list_ids` — so `verify.py` and the app work against
either without knowing which they have.

Section 10.1: the chain is optional for the science. If no deployment or RPC
is configured, callers fall back to the mock and records stay PENDING rather
than being shown as verified. `open_registry()` implements that choice in one
place.

Writes need a funded key and are owner-only at the contract. Reads need
nothing but an RPC URL, which is the common case for the verification page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .registry_client import CommitEntry, RegistryError

ARTIFACT = ("contracts/artifacts/contracts/ForecastRegistry.sol/"
            "ForecastRegistry.json")
DEPLOYMENT = "records/registry_deployment.json"


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp())


def load_abi(root: Path) -> list:
    path = root / ARTIFACT
    if not path.exists():
        raise RegistryError(
            "contract ABI not found at %s - run `npx hardhat compile` first"
            % path)
    return json.loads(path.read_text(encoding="utf-8"))["abi"]


def load_deployment(root: Path) -> dict | None:
    path = root / DEPLOYMENT
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class ChainInfo:
    """Where this client is pointed, for display and for the report."""
    address: str
    chain_id: int
    network: str
    rpc_url: str
    deploy_block: int | None = None
    deploy_tx: str | None = None

    @property
    def explorer_url(self) -> str | None:
        """Block explorer link, where one exists for this chain."""
        return {
            80002: "https://amoy.polygonscan.com/address/%s" % self.address,
            137: "https://polygonscan.com/address/%s" % self.address,
        }.get(self.chain_id)

    @property
    def is_public(self) -> bool:
        """A local dev chain is not something a third party can check."""
        return self.chain_id not in (31337, 1337)


class ChainRegistry:
    """The deployed contract, behind the MockRegistry interface."""

    def __init__(self, rpc_url: str, address: str, abi: list,
                 private_key: str | None = None, chain_id: int | None = None,
                 network: str = "unknown", deployment: dict | None = None):
        from web3 import Web3

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            raise RegistryError("cannot reach the RPC endpoint at " + rpc_url)

        self.address = self.w3.to_checksum_address(address)
        if self.w3.eth.get_code(self.address) in (b"", b"0x"):
            raise RegistryError(
                "no contract deployed at %s on this chain - check the address "
                "and that the RPC points at the right network" % self.address)

        self.contract = self.w3.eth.contract(address=self.address, abi=abi)
        self.account = (self.w3.eth.account.from_key(private_key)
                        if private_key else None)
        self.info = ChainInfo(
            address=self.address,
            chain_id=chain_id or self.w3.eth.chain_id,
            network=network,
            rpc_url=rpc_url,
            deploy_block=(deployment or {}).get("deploy_block"),
            deploy_tx=(deployment or {}).get("deploy_tx"),
        )

    # --- reads ---------------------------------------------------------------

    def get(self, forecast_id: str) -> CommitEntry | None:
        """The committed record, or None. Shape matches MockRegistry.get."""
        try:
            raw = self.contract.functions.getForecast(forecast_id).call()
        except Exception as exc:                      # pragma: no cover
            raise RegistryError("read failed for %s: %s" % (forecast_id, exc))

        forecast_hash, window_start, window_end, committed, outcome_hash, \
            outcome_at = raw[:6]

        if int(committed) == 0:            # never committed
            return None

        zero = b"\x00" * 32
        return CommitEntry(
            forecast_id=forecast_id,
            forecast_hash=forecast_hash.hex(),
            window_start_utc=_iso(int(window_start)),
            window_end_utc=_iso(int(window_end)),
            committed_utc=_iso(int(committed)),
            outcome_hash=(outcome_hash.hex() if outcome_hash != zero else None),
            outcome_recorded_utc=(_iso(int(outcome_at))
                                  if int(outcome_at) else None),
        )

    def list_ids(self) -> list[str]:
        total = int(self.contract.functions.count().call())
        out, step = [], 100
        for start in range(0, total, step):
            out.extend(self.contract.functions.idsSlice(start, step).call())
        return out

    def exists(self, forecast_id: str) -> bool:
        return bool(self.contract.functions.exists(forecast_id).call())

    # --- writes (owner only, needs a funded key) -----------------------------

    def _send(self, fn):
        if self.account is None:
            raise RegistryError(
                "this client is read-only: no private key configured. Set "
                "DEPLOYER_PRIVATE_KEY in .env to send transactions.")
        tx = fn.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "chainId": self.info.chain_id,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status != 1:                       # pragma: no cover
            raise RegistryError("transaction reverted: " + tx_hash.hex())
        return receipt

    def commit(self, forecast_id: str, forecast_hash: str,
               window_start_utc: str, window_end_utc: str) -> CommitEntry:
        """Commit on chain. The contract rejects any commit at or after the
        window opens, so historical forecasts cannot be committed live."""
        receipt = self._send(self.contract.functions.commit(
            forecast_id, bytes.fromhex(forecast_hash.removeprefix("0x")),
            _epoch(window_start_utc), _epoch(window_end_utc)))
        entry = self.get(forecast_id)
        if entry is None:                             # pragma: no cover
            raise RegistryError("commit succeeded but the record is not "
                                "readable: " + receipt.transactionHash.hex())
        return entry

    def record_outcome(self, forecast_id: str, outcome_hash: str) -> CommitEntry:
        self._send(self.contract.functions.recordOutcome(
            forecast_id, bytes.fromhex(outcome_hash.removeprefix("0x"))))
        return self.get(forecast_id)


# --- choosing a registry ------------------------------------------------------

class CompositeRegistry:
    """Chain first, mock second, per record.

    Records live in whichever registry they were committed to. Archived
    forecasts replayed with a simulated clock are on the mock, because the
    contract rightly refuses a commit once the window has opened; live
    forward-dated records are on chain. Asking only the chain would report
    the archived ones as PENDING, which is wrong - they were committed, just
    not there.

    `source_of()` says which registry answered, so the UI can label each
    record honestly rather than implying everything is on chain.
    """

    def __init__(self, chain, mock):
        self.chain = chain
        self.mock = mock

    def get(self, forecast_id: str) -> CommitEntry | None:
        if self.chain is not None:
            entry = self.chain.get(forecast_id)
            if entry is not None:
                return entry
        return self.mock.get(forecast_id)

    def source_of(self, forecast_id: str) -> str | None:
        """'chain', 'mock', or None if neither holds this record."""
        if self.chain is not None and self.chain.get(forecast_id) is not None:
            return "chain"
        if self.mock.get(forecast_id) is not None:
            return "mock"
        return None

    def list_ids(self) -> list[str]:
        ids = set(self.mock.list_ids())
        if self.chain is not None:
            ids |= set(self.chain.list_ids())
        return sorted(ids)

    @property
    def n_on_chain(self) -> int:
        return len(self.chain.list_ids()) if self.chain is not None else 0

    @property
    def n_on_mock(self) -> int:
        return len(self.mock.list_ids())


def open_registry(root: Path, records_dir: Path, prefer_chain: bool = True):
    """Return (registry, chain_info_or_None, reason).

    Uses the deployed contract when one is configured and reachable, but
    always keeps the mock as a second lookup so records committed there stay
    verifiable. The reason string is shown in the app so a reader always
    knows what they are looking at - silently falling back to a local file
    while implying a chain would be the worst outcome here.
    """
    from src.core.config import get_credential, load_dotenv_if_present

    from .registry_client import MockRegistry

    mock = MockRegistry(records_dir / "mock_registry.json")
    if not prefer_chain:
        return CompositeRegistry(None, mock), None, \
            "mock registry (chain not requested)"

    load_dotenv_if_present()
    deployment = load_deployment(root)
    address = get_credential("FORECAST_REGISTRY_ADDRESS") or (
        deployment or {}).get("address")
    rpc_url = (get_credential("AMOY_RPC_URL")
               or get_credential("EVM_RPC_URL")
               or (deployment or {}).get("rpc_url")
               or "http://127.0.0.1:8545")

    if not address:
        return CompositeRegistry(None, mock), None, (
            "mock registry - no contract address configured "
            "(set FORECAST_REGISTRY_ADDRESS or deploy)")

    try:
        chain = ChainRegistry(
            rpc_url=rpc_url, address=address, abi=load_abi(root),
            private_key=get_credential("DEPLOYER_PRIVATE_KEY"),
            chain_id=(deployment or {}).get("chain_id"),
            network=(deployment or {}).get("network", "unknown"),
            deployment=deployment)
    except Exception as exc:
        return (CompositeRegistry(None, mock), None,
                "mock registry - chain unreachable (%s)" % exc)

    composite = CompositeRegistry(chain, mock)
    return composite, chain.info, (
        "on-chain registry at %s (%s, chain %d) - %d record(s) on chain, "
        "%d on the local mock" % (chain.info.address, chain.info.network,
                                  chain.info.chain_id, composite.n_on_chain,
                                  composite.n_on_mock))
