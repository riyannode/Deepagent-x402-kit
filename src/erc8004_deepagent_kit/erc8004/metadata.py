from __future__ import annotations

import base64
import json
from typing import Any

REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"


def build_registration_file(
    *,
    name: str,
    description: str,
    image: str,
    services: list[dict[str, Any]],
    x402_support: bool,
    active: bool,
    registrations: list[dict[str, Any]] | None,
    supported_trust: list[str],
) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("name is required")
    if not description.strip():
        raise ValueError("description is required")
    if not image.strip():
        raise ValueError("image is required")
    if not isinstance(services, list):
        raise ValueError("services must be a list")
    for service in services:
        if not isinstance(service, dict):
            raise ValueError("each service must be an object")
        if "name" not in service or "endpoint" not in service:
            raise ValueError("each service must include name and endpoint")

    return {
        "type": REGISTRATION_TYPE,
        "name": name,
        "description": description,
        "image": image,
        "services": services,
        "x402Support": bool(x402_support),
        "active": bool(active),
        "registrations": registrations or [],
        "supportedTrust": supported_trust,
    }


def to_data_uri(registration_file: dict[str, Any]) -> str:
    raw = json.dumps(registration_file, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > 32768:
        raise ValueError(f"registration file exceeds 32KB limit ({len(raw)} bytes). Truncate description or services.")
    return "data:application/json;base64," + base64.b64encode(raw).decode("ascii")
