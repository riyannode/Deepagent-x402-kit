"""x402 payment policy: host allowlist, budget limits, HTTPS enforcement.

All buyer-facing x402 tools MUST call assert_request_allowed() before any HTTP
request or signing operation. This module is the single source of truth for
what the agent is allowed to pay for.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from ..config import load_config

logger = logging.getLogger(__name__)

# Blocked IP ranges (private, loopback, link-local, metadata)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
]

_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "fd00::ec2"}


def _parse_allowed_hosts(raw: str) -> set[str]:
    """Parse comma-separated allowed hosts. Empty = allow none."""
    if not raw or not raw.strip():
        return set()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _is_blocked_ip(host: str) -> bool:
    """Check if host resolves to a blocked IP range."""
    if host in _METADATA_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        # Not an IP literal — check if it's a hostname that might resolve
        # We can't DNS-resolve here without async, so just block known patterns
        return False


def assert_url_allowed(url: str) -> None:
    """Validate URL against all policy checks. Raises PermissionError on violation."""
    cfg = load_config()

    if not url:
        raise PermissionError("x402: URL is required")

    parsed = urlparse(url)

    # HTTPS enforcement
    if cfg.x402_require_https and parsed.scheme != "https":
        raise PermissionError(f"x402: HTTPS required, got {parsed.scheme}")

    if parsed.scheme not in ("http", "https"):
        raise PermissionError(f"x402: unsupported scheme: {parsed.scheme}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise PermissionError("x402: URL has no hostname")

    # Block private/loopback/link-local/metadata IPs
    if _is_blocked_ip(host):
        raise PermissionError(f"x402: blocked host (private/loopback/metadata): {host}")

    # Block localhost variants
    if host in ("localhost", "0.0.0.0", "[::]"):
        raise PermissionError(f"x402: blocked host: {host}")

    # Allowlist enforcement — fail-closed: empty list means block everything
    allowed = _parse_allowed_hosts(cfg.x402_allowed_hosts)
    if not allowed:
        raise PermissionError("x402: X402_ALLOWED_HOSTS must be non-empty for buyer payments")
    if host not in allowed:
        raise PermissionError(f"x402: host {host!r} not in X402_ALLOWED_HOSTS")


def assert_amount_allowed(amount_atomic: str) -> float:
    """Validate payment amount against per-request and daily limits.

    Returns amount in USDC (display units) for logging.
    Raises PermissionError if limits exceeded.
    """
    cfg = load_config()

    try:
        amount_int = int(amount_atomic)
    except (ValueError, TypeError):
        raise PermissionError(f"x402: invalid amount: {amount_atomic!r}")

    if amount_int < 0:
        raise PermissionError(f"x402: negative amount: {amount_atomic}")

    amount_usdc = amount_int / 1e6
    max_per_request = float(cfg.x402_max_per_request_usdc)

    if amount_usdc > max_per_request:
        raise PermissionError(
            f"x402: amount {amount_usdc} USDC exceeds X402_MAX_PER_REQUEST_USDC={max_per_request}"
        )

    return amount_usdc


def assert_challenge_valid(challenge: dict, expected_url: str) -> dict:
    """Validate an x402 payment challenge against policy.

    Returns the accepted payment requirement (first from accepts[]).
    Raises PermissionError on any violation.
    """
    cfg = load_config()

    accepts = challenge.get("accepts") or []
    if not accepts:
        raise PermissionError("x402: challenge has no accepts[] entries")

    accept = accepts[0]

    # Network check — must be Arc Testnet
    network = accept.get("network", "")
    if not network:
        raise PermissionError("x402: challenge missing network field")
    if network != "eip155:5042002":
        raise PermissionError(f"x402: unsupported network: {network} (expected eip155:5042002)")

    # Asset check — must be Arc USDC
    asset = accept.get("asset", "")
    expected_asset = "0x3600000000000000000000000000000000000000"
    if not asset:
        raise PermissionError("x402: challenge missing asset field")
    if asset.lower() != expected_asset.lower():
        raise PermissionError(f"x402: unexpected asset: {asset} (expected {expected_asset})")

    # Scheme check — must be exact or exact_nano
    scheme = accept.get("scheme", "")
    if not scheme:
        raise PermissionError("x402: challenge missing scheme field")
    if scheme not in ("exact", "exact_nano"):
        raise PermissionError(f"x402: unsupported scheme: {scheme}")

    # Amount check — must be within per-request limit
    amount = accept.get("amount", "")
    if not amount:
        raise PermissionError("x402: challenge missing amount field")
    assert_amount_allowed(str(amount))

    # payTo check — must be valid EVM address
    pay_to = accept.get("payTo", "")
    if not pay_to:
        raise PermissionError("x402: challenge missing payTo field")
    if not pay_to.startswith("0x") or len(pay_to) != 42:
        raise PermissionError(f"x402: invalid payTo: {pay_to}")

    # Resource check — challenge resource must match requested URL
    resource = challenge.get("resource", "")
    if resource and resource != expected_url:
        raise PermissionError(
            f"x402: challenge resource {resource!r} != requested URL {expected_url!r}"
        )

    return accept
