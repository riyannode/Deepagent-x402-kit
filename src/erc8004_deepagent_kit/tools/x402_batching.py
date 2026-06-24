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

from ..config import load_config, usdc_to_atomic
from ..x402.ledger import X402Ledger
from ..x402.policy import assert_amount_allowed, assert_challenge_valid, assert_url_allowed

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Seller input validation limits
_MAX_PAYMENT_SIGNATURE_BYTES = 8192  # base64-encoded x402 payload
_MAX_REQUEST_ID_LEN = 128
_MAX_RESOURCE_LEN = 2048


def _validate_seller_inputs(payment_signature: str, resource: str, request_id: str) -> None:
    """Validate seller tool inputs before passing to sidecar."""
    if not payment_signature or not isinstance(payment_signature, str):
        raise ValueError("payment_signature must be a non-empty string")
    if len(payment_signature) > _MAX_PAYMENT_SIGNATURE_BYTES:
        raise ValueError(f"payment_signature too large ({len(payment_signature)} > {_MAX_PAYMENT_SIGNATURE_BYTES})")
    # Basic base64 sanity check
    import base64
    try:
        base64.b64decode(payment_signature, validate=True)
    except Exception:
        raise ValueError("payment_signature must be valid base64")

    if not resource or not isinstance(resource, str):
        raise ValueError("resource must be a non-empty string")
    if len(resource) > _MAX_RESOURCE_LEN:
        raise ValueError(f"resource too large ({len(resource)} > {_MAX_RESOURCE_LEN})")
    if not resource.startswith(("http://", "https://")):
        raise ValueError("resource must be a valid URL")

    if not request_id or not isinstance(request_id, str):
        raise ValueError("request_id must be a non-empty string")
    if len(request_id) > _MAX_REQUEST_ID_LEN:
        raise ValueError(f"request_id too large ({len(request_id)} > {_MAX_REQUEST_ID_LEN})")


def _extract_amount_from_payment_payload(payment_signature: str) -> str | None:
    """Extract amount from base64-encoded x402 payment payload.

    Decodes the payment signature and extracts the authorization value.
    Returns the amount as a string, or None if extraction fails.
    """
    import base64
    try:
        decoded = base64.b64decode(payment_signature)
        payload = json.loads(decoded)
        # x402 payload structure: { payload: { authorization: { value: "..." } } }
        value = (payload.get("payload") or {}).get("authorization", {}).get("value")
        if value is not None:
            return str(value)
        # Fallback: check accepted.amount
        accepted = payload.get("accepted") or payload.get("payload", {}).get("accepted")
        if accepted and "amount" in accepted:
            return str(accepted["amount"])
    except Exception:
        pass
    return None


def _redact(text: str) -> str:
    """Strip known secrets from error messages."""
    cfg = load_config()
    s = text
    for secret in [cfg.circle_api_key, cfg.circle_entity_secret]:
        if secret:
            s = s.replace(secret, "[redacted]")
    return s


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

    try:
        proc = subprocess.run(
            ["node", str(script)],
            input=json.dumps(payload), text=True, capture_output=True,
            cwd=str(script.parent.parent), check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"x402 batching sidecar timed out after {timeout}s")
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"x402 batching sidecar failed: {_redact(proc.stderr[:500])}")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"x402 batching sidecar returned non-JSON: {proc.stdout[:200]}") from e
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

    agent_key = cfg.agent_key

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

    # F9: Reject challenge if amount is missing (don't default to max)
    amount_atomic = accept.get("amount")
    if not amount_atomic:
        raise PermissionError("x402: challenge missing amount field — refusing to default to max")
    assert_amount_allowed(str(amount_atomic))

    host = urlparse(url).hostname or ""
    resource = url
    request_id = hashlib.sha256(f"batch:{url}:{method}:{agent_key}".encode()).hexdigest()[:16]

    # F4: Atomic check+insert to prevent race condition
    ledger = X402Ledger()
    row_id = ledger.check_limits_and_insert_pending(
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
    # F13: Validate seller wallet is a proper EVM address
    if not ADDRESS_RE.match(pay_to):
        raise ValueError(f"X402_DEFAULT_SELLER_WALLET_ADDRESS is not a valid EVM address: {pay_to!r}")

    # Validate inputs before passing to sidecar
    _validate_seller_inputs(payment_signature, resource, request_id)

    # Extract actual amount from payment payload instead of hardcoding "1"
    extracted_amount = _extract_amount_from_payment_payload(payment_signature)
    amount_atomic = extracted_amount or "1"

    ledger = X402Ledger()

    # F10: Use full payment signature hash (not truncated)
    payment_hash = hashlib.sha256(
            f"sell:{payment_signature}:{pay_to}:{resource}:{request_id}".encode()
        ).hexdigest()
    existing = ledger.check_already_settled(payment_hash)
    if existing in ("success", "already_settled"):
        return {"ok": True, "mode": "batch_sell", "status": "already_settled", "payment_hash": payment_hash}

    # Insert pending
    row_id = ledger.insert_pending(
        mode="batch_sell", agent_key="seller", wallet_id="seller",
        host=urlparse(resource).hostname or "", resource=resource,
        request_id=request_id, amount_atomic=amount_atomic,
    )

    try:
        result = _run({
            "mode": "sell", "paymentSignature": payment_signature,
            "payTo": pay_to, "amountAtomic": amount_atomic, "resource": resource,
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


@tool
def gateway_deposit(amount_usdc: str) -> dict:
    """Deposit USDC to Circle Gateway for x402 payments.

    Transfers USDC from the configured DCW wallet to the Gateway wallet.
    The Gateway balance is used for x402 payment batching — without a
    balance, x402 payments will fail.

    Args:
        amount_usdc: Amount in USDC (e.g. "0.01" for 1 cent)

    Returns:
        Transaction result with tx_hash and explorer_url.
    """
    from ..wallet.contract_executor import CircleNodeSidecarExecutor
    from ..wallet.dcw import get_configured_wallet
    from ..wallet.policy import ContractCallIntent, WalletPolicy

    cfg = load_config()
    wallet = get_configured_wallet()

    # Convert USDC display units to atomic (6 decimals)
    try:
        amount_float = float(amount_usdc)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid amount_usdc: {amount_usdc!r}")
    if amount_float <= 0:
        raise ValueError("amount_usdc must be positive")
    if amount_float > 100:
        raise ValueError("amount_usdc exceeds safety limit (100 USDC)")

    amount_atomic = usdc_to_atomic(amount_usdc)

    USDC_ADDRESS = "0x3600000000000000000000000000000000000000"
    GATEWAY_ADDRESS = "0x0077777d7EBA4688BDeF3E311b846F25870A19B9"

    intent = ContractCallIntent(
        wallet_address=wallet.address,
        blockchain="ARC-TESTNET",
        contract_address=USDC_ADDRESS,
        abi_function_signature="transfer(address,uint256)",
        abi_parameters=[GATEWAY_ADDRESS, amount_atomic],
    )

    policy = WalletPolicy(
        identity_registry=cfg.identity_registry,
        reputation_registry=cfg.reputation_registry,
        validation_registry=cfg.validation_registry,
        enable_reputation_writes=cfg.enable_reputation_writes,
        enable_validation_writes=cfg.enable_validation_writes,
    )

    executor = CircleNodeSidecarExecutor(policy=policy)
    result = executor.execute(intent)

    explorer_url = f"{cfg.explorer_url}/tx/{result.tx_hash}"

    return {
        "ok": True,
        "mode": "gateway_deposit",
        "amount_usdc": amount_usdc,
        "amount_atomic": amount_atomic,
        "tx_hash": result.tx_hash,
        "explorer_url": explorer_url,
        "circle_transaction_id": result.circle_transaction_id,
        "gateway_address": GATEWAY_ADDRESS,
    }
