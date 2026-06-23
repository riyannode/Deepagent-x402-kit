from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = _env(name, str(default)) or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be <= {max_value}")
    return value


def _env_json(name: str, default: Any) -> Any:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc


def _require_address(value: str, name: str) -> str:
    if not Web3.is_address(value):
        raise ValueError(f"{name} must be a valid EVM address")
    return Web3.to_checksum_address(value)


def _require_urlish(value: str, name: str) -> str:
    value = value.strip()
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError(f"{name} must start with http:// or https://")
    return value


@dataclass(frozen=True)
class KitConfig:
    network_profile: str
    chain_id: int
    blockchain: str
    rpc_url: str
    explorer_url: str
    identity_registry: str
    reputation_registry: str
    validation_registry: str
    from_block: int
    event_scan_block_range: int
    circle_api_key: str | None
    circle_entity_secret: str | None
    dcw_wallet_address: str | None
    identity_store_path: Path
    agent_key: str
    agent_name: str
    agent_description: str
    agent_image: str
    agent_services: list[dict[str, Any]]
    agent_supported_trust: list[str]
    agent_x402_support: bool
    deepagent_model: str
    enable_reputation_writes: bool
    enable_validation_writes: bool
    circle_fee_level: str
    circle_tx_poll_seconds: int
    circle_tx_max_polls: int
    registration_lock_ttl_seconds: int
    verify_chain_id: bool
    expose_reputation_write_tools_to_agent: bool
    expose_validation_write_tools_to_agent: bool
    receipt_poll_seconds: int
    receipt_max_polls: int
    circle_execution_state_dir: Path

    @property
    def live_ready_env(self) -> bool:
        return bool(self.circle_api_key and self.circle_entity_secret and self.dcw_wallet_address)


_cached_config: KitConfig | None = None


def load_config(env_file: str | None = None) -> KitConfig:
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    load_dotenv(env_file or ".env", override=False)

    identity_registry = _require_address(
        _env("IDENTITY_REGISTRY", "0x8004A818BFB912233c491871b3d84c89A494BD9e") or "",
        "IDENTITY_REGISTRY",
    )
    reputation_registry = _require_address(
        _env("REPUTATION_REGISTRY", "0x8004B663056A597Dffe9eCcC1965A193B7388713") or "",
        "REPUTATION_REGISTRY",
    )
    validation_registry = _require_address(
        _env("VALIDATION_REGISTRY", "0x8004Cb1BF31DAf7788923b405b754f57acEB4272") or "",
        "VALIDATION_REGISTRY",
    )

    wallet = _env("DCW_WALLET_ADDRESS")
    if wallet:
        wallet = _require_address(wallet, "DCW_WALLET_ADDRESS")

    agent_services = _env_json("AGENT_SERVICES_JSON", [])
    if not isinstance(agent_services, list):
        raise ValueError("AGENT_SERVICES_JSON must be a JSON array")

    supported_trust = _env_json("AGENT_SUPPORTED_TRUST_JSON", ["reputation", "validation"])
    if not isinstance(supported_trust, list) or not all(isinstance(x, str) for x in supported_trust):
        raise ValueError("AGENT_SUPPORTED_TRUST_JSON must be a JSON array of strings")

    fee_level = (_env("CIRCLE_FEE_LEVEL", "MEDIUM") or "MEDIUM").upper()
    if fee_level not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("CIRCLE_FEE_LEVEL must be LOW, MEDIUM, or HIGH")

    rpc_url = _require_urlish(_env("RPC_URL", "https://rpc.drpc.testnet.arc.network") or "", "RPC_URL")
    explorer_url = _require_urlish(_env("EXPLORER_URL", "https://testnet.arcscan.app") or "", "EXPLORER_URL").rstrip("/")

    agent_key = _env("AGENT_KEY", "default-agent") or "default-agent"
    if len(agent_key.strip()) < 3:
        raise ValueError("AGENT_KEY must be at least 3 characters")
    if len(agent_key) > 128:
        raise ValueError("AGENT_KEY must be <= 128 characters")

    result = KitConfig(
        network_profile=_env("NETWORK_PROFILE", "arc-testnet") or "arc-testnet",
        chain_id=_env_int("CHAIN_ID", 5042002, min_value=1),
        blockchain=_env("BLOCKCHAIN", "ARC-TESTNET") or "ARC-TESTNET",
        rpc_url=rpc_url,
        explorer_url=explorer_url,
        identity_registry=identity_registry,
        reputation_registry=reputation_registry,
        validation_registry=validation_registry,
        from_block=_env_int("ERC8004_FROM_BLOCK", 41752050, min_value=0),
        event_scan_block_range=_env_int("EVENT_SCAN_BLOCK_RANGE", 10000, min_value=1, max_value=10000),
        circle_api_key=_env("CIRCLE_API_KEY"),
        circle_entity_secret=_env("CIRCLE_ENTITY_SECRET"),
        dcw_wallet_address=wallet,
        identity_store_path=Path(_env("IDENTITY_STORE_PATH", "/data/erc8004_identities.sqlite3") or "/data/erc8004_identities.sqlite3"),
        agent_key=agent_key,
        agent_name=_env("AGENT_NAME", "Example ERC-8004 Deep Agent") or "Example ERC-8004 Deep Agent",
        agent_description=_env("AGENT_DESCRIPTION", "LangChain Deep Agent with ERC-8004 tools.") or "LangChain Deep Agent with ERC-8004 tools.",
        agent_image=_env("AGENT_IMAGE", "https://example.com/agent.png") or "https://example.com/agent.png",
        agent_services=agent_services,
        agent_supported_trust=supported_trust,
        agent_x402_support=_env_bool("AGENT_X402_SUPPORT", False),
        deepagent_model=_env("DEEPAGENT_MODEL", "anthropic:claude-sonnet-4-6") or "anthropic:claude-sonnet-4-6",
        enable_reputation_writes=_env_bool("ENABLE_REPUTATION_WRITES", False),
        enable_validation_writes=_env_bool("ENABLE_VALIDATION_WRITES", False),
        circle_fee_level=fee_level,
        circle_tx_poll_seconds=_env_int("CIRCLE_TX_POLL_SECONDS", 5, min_value=1, max_value=60),
        circle_tx_max_polls=_env_int("CIRCLE_TX_MAX_POLLS", 180, min_value=1, max_value=300),
        registration_lock_ttl_seconds=_env_int("REGISTRATION_LOCK_TTL_SECONDS", 300, min_value=60, max_value=86400),
        verify_chain_id=_env_bool("VERIFY_CHAIN_ID", True),
        expose_reputation_write_tools_to_agent=_env_bool("EXPOSE_REPUTATION_WRITE_TOOLS_TO_AGENT", False),
        expose_validation_write_tools_to_agent=_env_bool("EXPOSE_VALIDATION_WRITE_TOOLS_TO_AGENT", False),
        receipt_poll_seconds=_env_int("RECEIPT_POLL_SECONDS", 3, min_value=1, max_value=60),
        receipt_max_polls=_env_int("RECEIPT_MAX_POLLS", 60, min_value=1, max_value=300),
        circle_execution_state_dir=Path(_env("CIRCLE_EXECUTION_STATE_DIR", "/data/circle_executions") or "/data/circle_executions"),
    )
    _cached_config = result
    return result
