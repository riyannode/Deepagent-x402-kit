from __future__ import annotations

from eth_utils import keccak, to_hex
from langchain_core.tools import tool
from web3 import Web3

from ..config import load_config
from ..wallet.contract_executor import CircleNodeSidecarExecutor
from ..wallet.dcw import get_configured_wallet
from ..wallet.policy import ContractCallIntent, WalletPolicy


def _policy() -> WalletPolicy:
    cfg = load_config()
    return WalletPolicy(
        identity_registry=cfg.identity_registry,
        reputation_registry=cfg.reputation_registry,
        validation_registry=cfg.validation_registry,
        enable_reputation_writes=cfg.enable_reputation_writes,
        enable_validation_writes=cfg.enable_validation_writes,
    )


@tool
def get_reputation_summary(agent_id: str) -> dict:
    """Return reputation registry configuration for an agent. Aggregation is intentionally off-chain/indexer-specific."""
    cfg = load_config()
    return {
        "agent_id": agent_id,
        "chain_id": cfg.chain_id,
        "reputation_registry": cfg.reputation_registry,
        "write_enabled": cfg.enable_reputation_writes,
        "note": "Use an indexer for full feedback aggregation; direct write tool is policy-gated.",
    }


@tool
def get_feedback_for_agent(agent_id: str) -> dict:
    """Placeholder read tool for reputation feedback. Use an indexer for production aggregation."""
    cfg = load_config()
    return {
        "agent_id": agent_id,
        "reputation_registry": cfg.reputation_registry,
        "feedback": [],
        "note": "This SDK includes the registry write ABI but does not ship a reputation indexer.",
    }


@tool
def record_reputation_feedback(agent_id: str, score: int, feedback_type: int, tag: str, metadata_uri: str = "", evidence_uri: str = "", comment: str = "") -> dict:
    """Policy-gated ERC-8004 reputation write through Circle DCW. Disabled unless ENABLE_REPUTATION_WRITES=true."""
    # Validate ranges before submission
    if not (-2**127 <= score < 2**127):
        raise ValueError(f"score out of int128 range: {score}")
    if not (0 <= feedback_type <= 255):
        raise ValueError(f"feedback_type out of uint8 range: {feedback_type}")
    try:
        int(agent_id)
    except (ValueError, TypeError):
        raise ValueError(f"agent_id must be a numeric token ID, got: {agent_id!r}")

    cfg = load_config()
    wallet = get_configured_wallet()
    feedback_hash = to_hex(keccak(text=tag))
    result = CircleNodeSidecarExecutor(policy=_policy()).execute(
        ContractCallIntent(
            wallet_address=wallet.address,
            blockchain=cfg.blockchain,
            contract_address=cfg.reputation_registry,
            abi_function_signature="giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)",
            abi_parameters=[str(agent_id), str(score), str(feedback_type), tag, metadata_uri, evidence_uri, comment, feedback_hash],
        )
    )
    return {
        "status": "feedback_recorded",
        "agent_id": agent_id,
        "wallet_address": Web3.to_checksum_address(wallet.address),
        "tx_hash": result.tx_hash,
        "explorer_url": f"{cfg.explorer_url}/tx/{result.tx_hash}",
    }
