from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


_PLACEHOLDER_VALUES = {"", "***", "REPLACE_ME", "CHANGEME", "CHANGE_ME", "YOUR_API_KEY_HERE", "YOUR_KEY_HERE", "YOUR_SECRET_HERE", "TODO"}


def _is_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip()
    if not normalized:
        return True
    upper = normalized.upper().replace("-", "_").replace(" ", "_")
    return upper in _PLACEHOLDER_VALUES or upper.startswith("YOUR_") or upper.startswith("REPLACE_")


def _env_secret(name: str) -> str | None:
    value = _env(name)
    if _is_placeholder_secret(value):
        return None
    return value

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


def _validate_seller_address(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not Web3.is_address(value):
        raise ValueError("X402_DEFAULT_SELLER_WALLET_ADDRESS must be a valid EVM address")
    return Web3.to_checksum_address(value)


def usdc_to_atomic(value: str) -> str:
    """Convert USDC display amount (e.g. '0.29') to atomic units (6 decimals).

    Uses Decimal to avoid floating-point precision loss.
    Truncates (ROUND_DOWN) to avoid overpaying.
    """
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid USDC amount: {value!r}") from exc
    if amount <= 0:
        raise ValueError("amount must be positive")
    atomic = (amount * Decimal("1000000")).to_integral_exact(rounding=ROUND_DOWN)
    return str(int(atomic))


def atomic_to_usdc(value: str) -> str:
    """Convert atomic units to USDC display amount."""
    try:
        atomic = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid atomic amount: {value!r}") from exc
    return str(atomic / Decimal("1000000"))


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
    reputation_store_path: Path
    reputation_indexer_from_block: int
    reputation_indexer_block_range: int
    reputation_writer_wallet_address: str | None
    agent_key: str
    agent_name: str
    agent_description: str
    agent_image: str
    agent_services: list[dict[str, Any]]
    agent_supported_trust: list[str]
    agent_x402_support: bool
    # x402 payment settings
    x402_enabled: bool
    x402_mode: str  # "batching" or "nano"
    x402_default_buyer_wallet_id: str | None
    x402_default_seller_wallet_address: str | None
    x402_max_per_request_usdc: str
    x402_max_daily_usdc: str
    x402_max_requests_per_day: int
    x402_allowed_hosts: str
    x402_require_https: bool
    x402_gateway_api_url: str
    x402_ledger_path: Path
    # x402 agent exposure
    x402_expose_balance_to_agent: bool
    x402_expose_batch_buyer_to_agent: bool
    x402_expose_batch_seller_to_agent: bool
    x402_expose_nano_buyer_to_agent: bool
    x402_expose_nano_seller_to_agent: bool
    x402_expose_gateway_deposit_to_agent: bool
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

    reputation_writer_wallet = _env("REPUTATION_WRITER_WALLET_ADDRESS")
    if reputation_writer_wallet:
        reputation_writer_wallet = _require_address(reputation_writer_wallet, "REPUTATION_WRITER_WALLET_ADDRESS")

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
        from_block=_env_int("ERC8004_FROM_BLOCK", 41338000, min_value=0),
        event_scan_block_range=_env_int("EVENT_SCAN_BLOCK_RANGE", 10000, min_value=1, max_value=10000),
        circle_api_key=_env_secret("CIRCLE_API_KEY"),
        circle_entity_secret=_env_secret("CIRCLE_ENTITY_SECRET"),
        dcw_wallet_address=wallet,
        identity_store_path=Path(_env("IDENTITY_STORE_PATH", "/data/erc8004_identities.sqlite3") or "/data/erc8004_identities.sqlite3"),
        reputation_store_path=Path(_env("REPUTATION_STORE_PATH", "/data/erc8004_reputation.sqlite3") or "/data/erc8004_reputation.sqlite3"),
        reputation_indexer_from_block=_env_int("REPUTATION_INDEXER_FROM_BLOCK", _env_int("ERC8004_FROM_BLOCK", 41338000, min_value=0), min_value=0),
        reputation_indexer_block_range=_env_int("REPUTATION_INDEXER_BLOCK_RANGE", 10000, min_value=1, max_value=10000),
        reputation_writer_wallet_address=reputation_writer_wallet,
        agent_key=agent_key,
        agent_name=_env("AGENT_NAME", "Example ERC-8004 Deep Agent") or "Example ERC-8004 Deep Agent",
        agent_description=_env("AGENT_DESCRIPTION", "LangChain Deep Agent with ERC-8004 tools.") or "LangChain Deep Agent with ERC-8004 tools.",
        agent_image=_env("AGENT_IMAGE", "https://example.com/agent.png") or "https://example.com/agent.png",
        agent_services=agent_services,
        agent_supported_trust=supported_trust,
        agent_x402_support=_env_bool("AGENT_X402_SUPPORT", False),
        x402_enabled=_env_bool("X402_ENABLED", False),
        x402_mode=(_env("X402_MODE", "batching") or "batching").lower(),
        x402_default_buyer_wallet_id=_env("X402_DEFAULT_BUYER_WALLET_ID"),
        x402_default_seller_wallet_address=_validate_seller_address(_env("X402_DEFAULT_SELLER_WALLET_ADDRESS")),
        x402_max_per_request_usdc=_env("X402_MAX_PER_REQUEST_USDC", "0.000001") or "0.000001",
        x402_max_daily_usdc=_env("X402_MAX_DAILY_USDC", "0.01") or "0.01",
        x402_max_requests_per_day=_env_int("X402_MAX_REQUESTS_PER_DAY", 100, min_value=1, max_value=10000),
        x402_allowed_hosts=_env("X402_ALLOWED_HOSTS", "") or "",
        x402_require_https=_env_bool("X402_REQUIRE_HTTPS", True),
        x402_gateway_api_url=_env("X402_GATEWAY_API_URL", "https://gateway-api-testnet.circle.com") or "https://gateway-api-testnet.circle.com",
        x402_ledger_path=Path(_env("X402_LEDGER_PATH", "/data/x402_spend_ledger.sqlite3") or "/data/x402_spend_ledger.sqlite3"),
        x402_expose_balance_to_agent=_env_bool("X402_EXPOSE_BALANCE_TO_AGENT", True),
        x402_expose_batch_buyer_to_agent=_env_bool("X402_EXPOSE_BATCH_BUYER_TO_AGENT", False),
        x402_expose_batch_seller_to_agent=_env_bool("X402_EXPOSE_BATCH_SELLER_TO_AGENT", False),
        x402_expose_nano_buyer_to_agent=_env_bool("X402_EXPOSE_NANO_BUYER_TO_AGENT", False),
        x402_expose_nano_seller_to_agent=_env_bool("X402_EXPOSE_NANO_SELLER_TO_AGENT", False),
        x402_expose_gateway_deposit_to_agent=_env_bool("X402_EXPOSE_GATEWAY_DEPOSIT_TO_AGENT", False),
        deepagent_model=_env("DEEPAGENT_MODEL", "anthropic:claude-sonnet-4-6") or "anthropic:claude-sonnet-4-6",
        enable_reputation_writes=_env_bool("ENABLE_REPUTATION_WRITES", False),
        enable_validation_writes=_env_bool("ENABLE_VALIDATION_WRITES", False),
        circle_fee_level=fee_level,
        circle_tx_poll_seconds=_env_int("CIRCLE_TX_POLL_SECONDS", 5, min_value=1, max_value=60),
        circle_tx_max_polls=_env_int("CIRCLE_TX_MAX_POLLS", 180, min_value=1, max_value=300),
        registration_lock_ttl_seconds=_env_int("REGISTRATION_LOCK_TTL_SECONDS", 1260, min_value=60, max_value=86400),
        verify_chain_id=_env_bool("VERIFY_CHAIN_ID", True),
        expose_reputation_write_tools_to_agent=_env_bool("EXPOSE_REPUTATION_WRITE_TOOLS_TO_AGENT", False),
        expose_validation_write_tools_to_agent=_env_bool("EXPOSE_VALIDATION_WRITE_TOOLS_TO_AGENT", False),
        receipt_poll_seconds=_env_int("RECEIPT_POLL_SECONDS", 3, min_value=1, max_value=60),
        receipt_max_polls=_env_int("RECEIPT_MAX_POLLS", 60, min_value=1, max_value=300),
        circle_execution_state_dir=Path(_env("CIRCLE_EXECUTION_STATE_DIR", "/data/circle_executions") or "/data/circle_executions"),
    )
    _cached_config = result
    return result
