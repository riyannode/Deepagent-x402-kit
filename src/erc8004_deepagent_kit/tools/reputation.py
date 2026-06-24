from __future__ import annotations

import re

from langchain_core.tools import tool
from web3 import Web3

from ..config import load_config
from ..erc8004.registry_clients import IdentityRegistryClient, ReputationRegistryClient
from ..store.reputation_store import ReputationStore
from ..wallet.contract_executor import CircleNodeSidecarExecutor
from ..wallet.dcw import get_reputation_writer_wallet
from ..wallet.policy import ContractCallIntent, WalletPolicy

ZERO_BYTES32 = "0x" + "0" * 64
BYTES32_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _policy() -> WalletPolicy:
    cfg = load_config()
    return WalletPolicy(identity_registry=cfg.identity_registry, reputation_registry=cfg.reputation_registry, validation_registry=cfg.validation_registry, enable_reputation_writes=cfg.enable_reputation_writes, enable_validation_writes=cfg.enable_validation_writes)


def _client() -> ReputationRegistryClient:
    cfg = load_config(); return ReputationRegistryClient(cfg.rpc_url, cfg.reputation_registry)


def _store() -> ReputationStore:
    cfg = load_config()
    return ReputationStore(cfg.reputation_store_path, cfg.chain_id, cfg.reputation_registry)


def _agent_id(agent_id: str) -> str:
    if int(agent_id) < 0: raise ValueError("agent_id must be numeric")
    return str(agent_id)


def _hash(value: str) -> str:
    if not value: return ZERO_BYTES32
    if not BYTES32_RE.match(value): raise ValueError("hash must be 32-byte hex")
    return value


def _check_len(name: str, value: str, limit: int) -> None:
    if len(value) > limit: raise ValueError(f"{name} must be <= {limit} characters")


def _writer_intent(sig: str, params: list) -> dict:
    cfg = load_config(); wallet = get_reputation_writer_wallet()
    result = CircleNodeSidecarExecutor(policy=_policy()).execute(ContractCallIntent(wallet_address=wallet.address, blockchain=cfg.blockchain, contract_address=cfg.reputation_registry, abi_function_signature=sig, abi_parameters=params))
    return {"tx_hash": result.tx_hash, "explorer_url": f"{cfg.explorer_url}/tx/{result.tx_hash}", "wallet_address": Web3.to_checksum_address(wallet.address)}


def _checksum_client_addresses(client_addresses: list[str] | None) -> list[str] | None:
    if client_addresses is None:
        return None
    out: list[str] = []
    for address in client_addresses:
        if not Web3.is_address(address):
            raise ValueError(f"invalid client address: {address}")
        out.append(Web3.to_checksum_address(address))
    return out


def _assert_reputation_writer_allowed_for_agent(agent_id: str):
    aid = _agent_id(agent_id)
    cfg = load_config()
    wallet = get_reputation_writer_wallet()
    identity = IdentityRegistryClient(cfg.rpc_url, cfg.identity_registry)
    owner = identity.owner_of(aid)
    writer = Web3.to_checksum_address(wallet.address)
    if writer == Web3.to_checksum_address(owner):
        raise PermissionError("REPUTATION_WRITER_WALLET_ADDRESS must not equal the target agent owner wallet")
    approved = identity.get_approved(aid)
    if approved and writer == Web3.to_checksum_address(approved):
        raise PermissionError("REPUTATION_WRITER_WALLET_ADDRESS must not be approved for the target agent token")
    if identity.is_approved_for_all(owner, writer):
        raise PermissionError("REPUTATION_WRITER_WALLET_ADDRESS must not be an operator for the target agent owner")
    return wallet


@tool
def get_reputation_summary(agent_id: str, client_addresses: list[str], tag1: str = "", tag2: str = "") -> dict:
    """Read ERC-8004 reputation summary using explicit trusted client addresses."""
    return _client().get_summary(agent_id, client_addresses, tag1, tag2)


@tool
def get_feedback_for_agent(agent_id: str, client_addresses: list[str] | None = None, tag1: str = "", tag2: str = "", include_revoked: bool = False, limit: int = 50, offset: int = 0) -> dict:
    """Read indexed ERC-8004 feedback for an agent from the local reputation indexer store."""
    store = _store(); status = store.status()
    if status["state"] == "indexer_required":
        return {"agent_id": agent_id, "feedback_available": False, "feedback": [], "indexer_status": status}
    rows = store.list_feedback(agent_id, _checksum_client_addresses(client_addresses), tag1, tag2, include_revoked, min(int(limit), 100), int(offset))
    return {"agent_id": agent_id, "feedback_available": True, "feedback": rows, "indexer_status": status}


@tool
def read_reputation_feedback(agent_id: str, client_address: str, feedback_index: int) -> dict:
    """Read one ERC-8004 reputation feedback from contract and merge indexed event metadata if available."""
    client_address = Web3.to_checksum_address(client_address)
    onchain = _client().read_feedback(agent_id, client_address, feedback_index)
    indexed = _store().get_feedback(agent_id, client_address, feedback_index)
    responses = _store().get_responses(agent_id, client_address, feedback_index)
    if indexed:
        onchain.update({k: indexed[k] for k in ("endpoint", "feedback_uri", "feedback_hash", "tx_hash", "block_number", "log_index") if k in indexed})
    onchain["responses"] = responses
    return onchain


@tool
def get_reputation_clients(agent_id: str) -> dict:
    """Read clients who gave feedback to an ERC-8004 agent."""
    clients = []
    try:
        clients.extend(_client().get_clients(agent_id))
    except Exception as exc:
        contract_error = str(exc)
    else:
        contract_error = None
    clients.extend(_store().list_clients(agent_id))
    dedup = sorted({Web3.to_checksum_address(c) for c in clients if Web3.is_address(c)})
    return {"agent_id": agent_id, "clients": dedup, "contract_error": contract_error}


@tool
def get_reputation_indexer_status() -> dict:
    """Return local reputation indexer status without sending transactions."""
    return _store().status()


@tool
def record_reputation_feedback(agent_id: str, value: int, value_decimals: int, tag1: str = "", tag2: str = "", endpoint: str = "", feedback_uri: str = "", feedback_hash: str = "") -> dict:
    """Policy-gated ERC-8004 reputation write through Circle DCW. Disabled unless ENABLE_REPUTATION_WRITES=true and EXPOSE_REPUTATION_WRITE_TOOLS_TO_AGENT=true."""
    aid = _agent_id(agent_id)
    if not (-2**127 <= int(value) < 2**127): raise ValueError("value out of int128 range")
    if not (0 <= int(value_decimals) <= 18): raise ValueError("value_decimals must be between 0 and 18")
    _check_len("tag1", tag1, 128); _check_len("tag2", tag2, 128); _check_len("endpoint", endpoint, 2048); _check_len("feedback_uri", feedback_uri, 2048)
    _assert_reputation_writer_allowed_for_agent(aid)
    out = _writer_intent("giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)", [aid, str(int(value)), str(int(value_decimals)), tag1, tag2, endpoint, feedback_uri, _hash(feedback_hash)])
    return {"status": "feedback_recorded", "agent_id": aid, "value": int(value), "value_decimals": int(value_decimals), "tag1": tag1, "tag2": tag2, **out}


@tool
def revoke_reputation_feedback(agent_id: str, feedback_index: int) -> dict:
    """Policy-gated ERC-8004 revokeFeedback write through Circle DCW."""
    aid = _agent_id(agent_id)
    if int(feedback_index) <= 0: raise ValueError("feedback_index must be > 0")
    _assert_reputation_writer_allowed_for_agent(aid)
    out = _writer_intent("revokeFeedback(uint256,uint64)", [aid, str(int(feedback_index))])
    return {"status": "feedback_revoked", "agent_id": aid, "feedback_index": int(feedback_index), **out}


@tool
def append_reputation_response(agent_id: str, client_address: str, feedback_index: int, response_uri: str = "", response_hash: str = "") -> dict:
    """Policy-gated ERC-8004 appendResponse write through Circle DCW."""
    aid = _agent_id(agent_id)
    client = Web3.to_checksum_address(client_address)
    if int(feedback_index) <= 0: raise ValueError("feedback_index must be > 0")
    _check_len("response_uri", response_uri, 2048)
    _assert_reputation_writer_allowed_for_agent(aid)
    out = _writer_intent("appendResponse(uint256,address,uint64,string,bytes32)", [aid, client, str(int(feedback_index)), response_uri, _hash(response_hash)])
    return {"status": "response_appended", "agent_id": aid, "client_address": client, "feedback_index": int(feedback_index), **out}
