from __future__ import annotations

from deepagents import create_deep_agent

from .config import load_config
from .deepagent.system_prompt import SYSTEM_PROMPT
from .tools.identity import get_agent_metadata, get_agent_wallet, get_identity_status, register_identity_once
from .tools.registry_status import get_erc8004_config
from .tools.reputation import append_reputation_response, get_feedback_for_agent, get_reputation_clients, get_reputation_indexer_status, get_reputation_summary, read_reputation_feedback, record_reputation_feedback, revoke_reputation_feedback
from .tools.validation import get_validation_status, request_validation, submit_validation_response
from .tools.x402_batching import gateway_deposit, x402_batch_balance, x402_batch_pay, x402_batch_sell_settle
from .tools.x402_nano import x402_nano_balance, x402_nano_pay, x402_nano_sell_settle


def build_erc8004_deep_agent(model: str | None = None):
    cfg = load_config()
    tools = [
        get_erc8004_config,
        get_identity_status,
        register_identity_once,
        get_agent_metadata,
        get_agent_wallet,
        get_reputation_summary,
        get_feedback_for_agent,
        read_reputation_feedback,
        get_reputation_clients,
        get_reputation_indexer_status,
        get_validation_status,
    ]

    if cfg.enable_validation_writes and cfg.expose_validation_write_tools_to_agent:
        tools.extend([request_validation, submit_validation_response])

    if cfg.enable_reputation_writes and cfg.expose_reputation_write_tools_to_agent:
        tools.extend([record_reputation_feedback, revoke_reputation_feedback, append_reputation_response])

    # x402 payment tools — granular exposure
    if cfg.x402_enabled:
        if cfg.x402_expose_gateway_deposit_to_agent:
            tools.append(gateway_deposit)

        if cfg.x402_expose_balance_to_agent:
            tools.extend([x402_batch_balance, x402_nano_balance])

        if cfg.x402_mode == "batching":
            if cfg.x402_expose_batch_buyer_to_agent:
                tools.append(x402_batch_pay)
            if cfg.x402_expose_batch_seller_to_agent:
                tools.append(x402_batch_sell_settle)

        if cfg.x402_mode == "nano":
            if cfg.x402_expose_nano_buyer_to_agent:
                tools.append(x402_nano_pay)
            if cfg.x402_expose_nano_seller_to_agent:
                tools.append(x402_nano_sell_settle)

    return create_deep_agent(
        model=model or cfg.deepagent_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
