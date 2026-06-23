from __future__ import annotations

from eth_utils import keccak, to_hex
from langchain_core.tools import tool
from web3 import Web3

from ..config import load_config
from ..erc8004.registry_clients import ValidationRegistryClient
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
def get_validation_status(request_hash: str) -> dict:
    """Read ERC-8004 validation status by requestHash."""
    cfg = load_config()
    return ValidationRegistryClient(cfg.rpc_url, cfg.validation_registry).get_validation_status(request_hash)


@tool
def request_validation(validator_address: str, agent_id: str, request_uri: str, request_tag: str) -> dict:
    """Policy-gated ERC-8004 validationRequest write through Circle DCW. Disabled unless ENABLE_VALIDATION_WRITES=true."""
    try:
        int(agent_id)
    except (ValueError, TypeError):
        raise ValueError(f"agent_id must be a numeric token ID, got: {agent_id!r}")

    cfg = load_config()
    wallet = get_configured_wallet()
    validator_address = Web3.to_checksum_address(validator_address)
    request_hash = to_hex(keccak(text=f"{cfg.chain_id}:{cfg.validation_registry}:{agent_id}:{request_tag}:{request_uri}"))
    result = CircleNodeSidecarExecutor(policy=_policy()).execute(
        ContractCallIntent(
            wallet_address=wallet.address,
            blockchain=cfg.blockchain,
            contract_address=cfg.validation_registry,
            abi_function_signature="validationRequest(address,uint256,string,bytes32)",
            abi_parameters=[validator_address, str(agent_id), request_uri, request_hash],
        )
    )
    return {
        "status": "validation_requested",
        "agent_id": agent_id,
        "validator_address": validator_address,
        "request_hash": request_hash,
        "tx_hash": result.tx_hash,
        "explorer_url": f"{cfg.explorer_url}/tx/{result.tx_hash}",
    }


@tool
def submit_validation_response(request_hash: str, response: int, response_uri: str = "", response_tag: str = "validated") -> dict:
    """Policy-gated ERC-8004 validationResponse write through Circle DCW. Disabled unless ENABLE_VALIDATION_WRITES=true."""
    if not (0 <= response <= 255):
        raise ValueError(f"response out of uint8 range: {response}")

    cfg = load_config()
    wallet = get_configured_wallet()
    if not request_hash.startswith("0x") or len(request_hash) != 66:
        raise ValueError("request_hash must be bytes32 hex")
    response_hash = to_hex(keccak(text=f"{request_hash}:{response}:{response_tag}:{response_uri}"))
    result = CircleNodeSidecarExecutor(policy=_policy()).execute(
        ContractCallIntent(
            wallet_address=wallet.address,
            blockchain=cfg.blockchain,
            contract_address=cfg.validation_registry,
            abi_function_signature="validationResponse(bytes32,uint8,string,bytes32,string)",
            abi_parameters=[request_hash, str(response), response_uri, response_hash, response_tag],
        )
    )
    return {
        "status": "validation_response_submitted",
        "request_hash": request_hash,
        "response": response,
        "response_hash": response_hash,
        "wallet_address": wallet.address,
        "tx_hash": result.tx_hash,
        "explorer_url": f"{cfg.explorer_url}/tx/{result.tx_hash}",
    }
