"""x402 Batching tools — Circle x402-batching protocol for high-frequency agent commerce.

All buyer tools enforce:
  - Host allowlist (X402_ALLOWED_HOSTS)
  - HTTPS requirement (X402_REQUIRE_HTTPS)
  - Per-request max (X402_MAX_PER_REQUEST_USDC)
  - Daily budget (X402_MAX_DAILY_USDC)
  - Daily request count (X402_MAX_REQUESTS_PER_DAY)
  - Wallet from env only (X402_DEFAULT_BUYER_WALLET_ID) — not from LLM
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.tools import tool

from ..config import load_config
from ..x402.ledger import X402Ledger
from ..x402.policy import assert_amount_allowed, assert_challenge_valid, assert_url_allowed

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _sidecar() -> Path:
    cfg = load_config()
    p = Path(os.getenv("SDK_PROJECT_ROOT", "/app")) / "scripts" / "x402_batching.mjs"
    if not p.exists():
        raise RuntimeError(f"x402 batching sidecar not found: {p}")
    return p


def _run(payload: dict, timeout: int = 120) -> dict:
    script = _sidecar()
    cfg = load_config()
    if not cfg.circle_api_key or not cfg.circle_entity_secret:
        raise RuntimeError("CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET required")

    proc = subprocess.run(
        ["node", str(script)],
        input=json.dumps(payload), text=True, capture_output=True,
        cwd=str(script.parent.parent), check=False, timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"x402 batching sidecar failed: {proc.stderr[:500]}")
    result = json.loads(proc.stdout)
    if not result.get("ok"):
        raise RuntimeError(f"x402 batching failed: {result.get('error', 'unknown')}")
    return result


@tool
def x402_batch_pay(url: str, method: str = "GET") -> dict:
    """Buyer: pay for a Circle x402-batching protected endpoint.

    Uses configured X402_DEFAULT_BUYER_WALLET_ID from env.
    Enforces allowlist, max per request, daily budget, and request count.
    Does not accept wallet_id from the LLM.
    """
    cfg = load_config()

    # Policy checks BEFORE any HTTP request
    assert_url_allowed(url)

    buyer_wallet_id = cfg.x402_default_buyer_wallet_id
    if not buyer_wallet_id:
        raise RuntimeError("X402_DEFAULT_BUYER_WALLET_ID not configured")

    # Daily limits check
    ledger = X402Ledger()
    agent_key = cfg.agent_key
    ledger.check_daily_limits(agent_key, buyer_wallet_id)

    host = urlparse(url).hostname or ""
    resource = url
    request_id = hashlib.sha256(f"batch:{url}:{method}:{agent_key}".encode()).hexdigest()[:16]

    # Phase 1: prefetch the 402 challenge (no signing yet)
    prefetch_result = _run({
        "mode": "prefetch", "url": url, "method": method,
    })

    if not prefetch_result.get("paymentRequired"):
        # No payment needed — return the result directly
        return prefetch_result

    challenge = prefetch_result.get("challenge")
    if not challenge:
        raise RuntimeError("x402: prefetch returned no challenge")

    # Phase 2: validate challenge in Python BEFORE any signing
    accept = assert_challenge_valid(challenge, url)

    # Pre-validate amount against per-request max
    amount_atomic = accept.get("amount", str(int(float(cfg.x402_max_per_request_usdc) * 1e6)))
    assert_amount_allowed(str(amount_atomic))

    # Insert pending ledger row
    row_id = ledger.insert_pending(
        mode="batching", agent_key=agent_key, wallet_id=buyer_wallet_id,
        host=host, resource=resource, request_id=request_id,
        amount_atomic=str(amount_atomic),
    )

    try:
        # Phase 3: sign and retry with pre-validated challenge
        result = _run({
            "mode": "pay", "url": url, "walletId": buyer_wallet_id,
            "maxAmountUsdc": cfg.x402_max_per_request_usdc, "method": method,
            "challenge": challenge,
        })
        ledger.update_status(row_id, "success")
        result["ledger_row_id"] = row_id
        result["request_id"] = request_id
        return result
    except Exception as e:
        ledger.update_status(row_id, "failed")
        raise


@tool
def x402_batch_sell_settle(payment_signature: str, resource: str, request_id: str) -> dict:
    """Seller: verify and settle incoming Circle x402-batching payment.

    Uses replay/idempotency cache before settlement.
    """
    cfg = load_config()
    pay_to = cfg.x402_default_seller_wallet_address
    if not pay_to:
        raise RuntimeError("X402_DEFAULT_SELLER_WALLET_ADDRESS not configured")

    ledger = X402Ledger()

    # Idempotency check
    payment_hash = hashlib.sha256(
        f"sell:{payment_signature[:64]}:{pay_to}:{resource}:{request_id}".encode()
    ).hexdigest()
    existing = ledger.check_already_settled(payment_hash)
    if existing in ("success", "already_settled"):
        return {"ok": True, "mode": "batch_sell", "status": "already_settled", "payment_hash": payment_hash}

    # Insert pending
    row_id = ledger.insert_pending(
        mode="batch_sell", agent_key="seller", wallet_id="seller",
        host=urlparse(resource).hostname or "", resource=resource,
        request_id=request_id, amount_atomic="1",
    )

    try:
        result = _run({
            "mode": "sell", "paymentSignature": payment_signature,
            "payTo": pay_to, "amountAtomic": "1", "resource": resource,
        })
        tx_hash = result.get("txHash")
        ledger.update_status(row_id, "success", tx_hash=tx_hash)
        result["ledger_row_id"] = row_id
        result["payment_hash"] = payment_hash
        return result
    except Exception as e:
        ledger.update_status(row_id, "failed")
        raise


@tool
def x402_batch_balance(wallet_address: str) -> dict:
    """Read Circle Gateway USDC balance for a wallet. No payment."""
    if not ADDRESS_RE.match(wallet_address):
        raise ValueError(f"Invalid address: {wallet_address}")
    return _run({"mode": "balance", "walletAddress": wallet_address})
