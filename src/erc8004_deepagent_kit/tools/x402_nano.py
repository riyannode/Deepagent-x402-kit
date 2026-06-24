"""x402 Nanopayment Standalone tools — 1 request = 1 payment authorization.

Simpler than batching. Designed for single paid API calls, demos, lightweight endpoints.
Same security policy: host allowlist, budget limits, env-only wallet.
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
    p = Path(os.getenv("SDK_PROJECT_ROOT", "/app")) / "scripts" / "x402_nano.mjs"
    if not p.exists():
        raise RuntimeError(f"x402 nano sidecar not found: {p}")
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
        raise RuntimeError(f"x402 nano sidecar failed: {proc.stderr[:500]}")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"x402 nano sidecar returned non-JSON: {proc.stdout[:200]}") from e
    if not result.get("ok"):
        raise RuntimeError(f"x402 nano failed: {result.get('error', 'unknown')}")
    return result


@tool
def x402_nano_pay(url: str, method: str = "GET") -> dict:
    """Buyer: one HTTP request, one payment authorization.

    Uses configured buyer wallet only. Enforces same budget policy.
    """
    cfg = load_config()
    assert_url_allowed(url)

    buyer_wallet_id = cfg.x402_default_buyer_wallet_id
    if not buyer_wallet_id:
        raise RuntimeError("X402_DEFAULT_BUYER_WALLET_ID not configured")

    ledger = X402Ledger()
    agent_key = cfg.agent_key

    host = urlparse(url).hostname or ""
    request_id = hashlib.sha256(f"nano:{url}:{method}:{agent_key}".encode()).hexdigest()[:16]

    # Phase 1: prefetch the 402 challenge (no signing yet)
    prefetch_result = _run({
        "mode": "prefetch", "url": url, "method": method,
    })

    if not prefetch_result.get("paymentRequired"):
        return prefetch_result

    challenge = prefetch_result.get("challenge")
    if not challenge:
        raise RuntimeError("x402: prefetch returned no challenge")

    # Phase 2: validate challenge in Python BEFORE any signing
    accept = assert_challenge_valid(challenge, url)

    # F9: Reject challenge if amount is missing (don't default to max)
    amount_atomic = accept.get("amount")
    if not amount_atomic:
        raise PermissionError("x402: challenge missing amount field — refusing to default to max")
    assert_amount_allowed(str(amount_atomic))

    # F4: Atomic check+insert to prevent race condition
    ledger = X402Ledger()
    row_id = ledger.check_limits_and_insert_pending(
        mode="nano", agent_key=agent_key, wallet_id=buyer_wallet_id,
        host=host, resource=url, request_id=request_id,
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
    except Exception:
        ledger.update_status(row_id, "failed")
        raise


@tool
def x402_nano_sell_settle(payment_signature: str, resource: str, request_id: str) -> dict:
    """Seller: verify/settle one standalone nanopayment. Idempotent."""
    cfg = load_config()
    pay_to = cfg.x402_default_seller_wallet_address
    if not pay_to:
        raise RuntimeError("X402_DEFAULT_SELLER_WALLET_ADDRESS not configured")
    # F13: Validate seller wallet is a proper EVM address
    if not ADDRESS_RE.match(pay_to):
        raise ValueError(f"X402_DEFAULT_SELLER_WALLET_ADDRESS is not a valid EVM address: {pay_to!r}")

    ledger = X402Ledger()
    # F10: Use full payment signature hash (not truncated)
    payment_hash = hashlib.sha256(
            f"nano_sell:{payment_signature}:{pay_to}:{resource}:{request_id}".encode()
        ).hexdigest()
    existing = ledger.check_already_settled(payment_hash)
    if existing in ("success", "already_settled"):
        return {"ok": True, "mode": "nano_sell", "status": "already_settled", "payment_hash": payment_hash}

    row_id = ledger.insert_pending(
        mode="nano_sell", agent_key="seller", wallet_id="seller",
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
    except Exception:
        ledger.update_status(row_id, "failed")
        raise


@tool
def x402_nano_balance(wallet_address: str) -> dict:
    """Gateway balance read."""
    if not ADDRESS_RE.match(wallet_address):
        raise ValueError(f"Invalid address: {wallet_address}")
    return _run({"mode": "balance", "walletAddress": wallet_address})
