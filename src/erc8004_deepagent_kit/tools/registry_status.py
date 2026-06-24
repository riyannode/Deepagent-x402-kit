from __future__ import annotations

from langchain_core.tools import tool

from ..config import load_config


def _get_erc8004_config_impl() -> dict:
    cfg = load_config()
    return {
        "network_profile": cfg.network_profile,
        "chain_id": cfg.chain_id,
        "blockchain": cfg.blockchain,
        "rpc_url": cfg.rpc_url,
        "explorer_url": cfg.explorer_url,
        "identity_registry": cfg.identity_registry,
        "reputation_registry": cfg.reputation_registry,
        "validation_registry": cfg.validation_registry,
        "from_block": cfg.from_block,
        "event_scan_block_range": cfg.event_scan_block_range,
        "execution_mode": "live_circle_only",
        "verify_chain_id": cfg.verify_chain_id,
        "identity_store_path": str(cfg.identity_store_path),
        "writes": {
            "identity_registration": True,
            "reputation": cfg.enable_reputation_writes,
            "validation": cfg.enable_validation_writes,
        },
        "x402": {
            "enabled": cfg.x402_enabled,
            "mode": cfg.x402_mode,
            "max_per_request_usdc": cfg.x402_max_per_request_usdc,
            "max_daily_usdc": cfg.x402_max_daily_usdc,
            "max_requests_per_day": cfg.x402_max_requests_per_day,
            "gateway_api_url": cfg.x402_gateway_api_url,
            "buyer_wallet_configured": bool(cfg.x402_default_buyer_wallet_id),
            "seller_wallet_configured": bool(cfg.x402_default_seller_wallet_address),
            "expose": {
                "balance": cfg.x402_expose_balance_to_agent,
                "batch_buyer": cfg.x402_expose_batch_buyer_to_agent,
                "batch_seller": cfg.x402_expose_batch_seller_to_agent,
                "nano_buyer": cfg.x402_expose_nano_buyer_to_agent,
                "nano_seller": cfg.x402_expose_nano_seller_to_agent,
                "gateway_deposit": cfg.x402_expose_gateway_deposit_to_agent,
            },
        },
    }


@tool
def get_erc8004_config() -> dict:
    """Return safe ERC-8004 network and registry configuration. Does not reveal secrets."""
    return _get_erc8004_config_impl()
