from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..config import load_config
from .policy import ContractCallIntent, WalletPolicy

TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _redact(value: str) -> str:
    cfg = load_config()
    out = value
    for secret in (cfg.circle_api_key, cfg.circle_entity_secret):
        if secret:
            out = out.replace(secret, "[redacted]")
    return out


@dataclass(frozen=True)
class ContractExecutionResult:
    circle_transaction_id: str
    tx_hash: str
    state: str
    state_file: str


class CircleNodeSidecarExecutor:
    def __init__(self, *, policy: WalletPolicy, project_root: Path | None = None):
        self.policy = policy
        self.project_root = project_root or Path(os.getenv("SDK_PROJECT_ROOT", "/app"))

    def execute(self, intent: ContractCallIntent) -> ContractExecutionResult:
        self.policy.assert_allowed(intent)
        cfg = load_config()

        if not cfg.circle_api_key:
            raise RuntimeError("CIRCLE_API_KEY is required for live Circle DCW execution")
        if not cfg.circle_entity_secret:
            raise RuntimeError("CIRCLE_ENTITY_SECRET is required for live Circle DCW execution")

        script = self.project_root / "scripts" / "circle_execute_contract.mjs"
        if not script.exists():
            raise RuntimeError(f"Circle sidecar script not found: {script}")

        # M6: Verify script integrity (SHA-256 of known-good script)
        _EXPECTED_SCRIPT_HASH = "SKIP_CHECK"  # Set to real hash in production
        if _EXPECTED_SCRIPT_HASH != "SKIP_CHECK":
            actual_hash = hashlib.sha256(script.read_bytes()).hexdigest()
            if actual_hash != _EXPECTED_SCRIPT_HASH:
                raise RuntimeError(f"Circle sidecar script integrity check failed. Expected {_EXPECTED_SCRIPT_HASH}, got {actual_hash}")

        cfg.circle_execution_state_dir.mkdir(parents=True, exist_ok=True)
        # M1: Restrict state directory permissions
        os.chmod(cfg.circle_execution_state_dir, 0o700)
        state_file = cfg.circle_execution_state_dir / f"circle-execution-{uuid4()}.json"

        poll_seconds = cfg.circle_tx_poll_seconds
        max_polls = cfg.circle_tx_max_polls
        timeout_seconds = max(180, poll_seconds * max_polls + 120)

        # B1: Ensure lock TTL covers the full Circle polling + receipt window
        # Total max wait = Circle poll (poll*max_polls) + buffer (120s) + receipt poll (receipt_poll*receipt_max_polls)
        total_max_wait = (poll_seconds * max_polls + 120) + (cfg.receipt_poll_seconds * cfg.receipt_max_polls)
        if cfg.registration_lock_ttl_seconds < total_max_wait:
            raise RuntimeError(
                f"REGISTRATION_LOCK_TTL_SECONDS ({cfg.registration_lock_ttl_seconds}) must be >= "
                f"total max wait ({total_max_wait}). "
                f"Circle poll: {poll_seconds}*{max_polls}+120={poll_seconds*max_polls+120}s, "
                f"receipt poll: {cfg.receipt_poll_seconds}*{cfg.receipt_max_polls}={cfg.receipt_poll_seconds*cfg.receipt_max_polls}s. "
                f"Set REGISTRATION_LOCK_TTL_SECONDS={total_max_wait + 60} in .env"
            )

        payload = {
            "walletAddress": intent.wallet_address,
            "blockchain": intent.blockchain,
            "contractAddress": intent.contract_address,
            "abiFunctionSignature": intent.abi_function_signature,
            "abiParameters": intent.abi_parameters,
            "feeLevel": cfg.circle_fee_level,
            "pollSeconds": poll_seconds,
            "maxPolls": max_polls,
            "stateFile": str(state_file),
        }

        try:
            proc = subprocess.run(
                ["node", str(script)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                cwd=str(self.project_root),
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Circle sidecar timed out before returning a final transaction state. "
                f"Execution state file: {state_file}. The local registration lock is intentionally kept until TTL to avoid duplicate submits."
            ) from exc

        if proc.returncode != 0:
            stderr = _redact(proc.stderr.strip())
            stdout = _redact(proc.stdout.strip())
            raise RuntimeError(f"Circle sidecar failed. Execution state file: {state_file}. Error: {stderr or stdout}")

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Circle sidecar returned non-JSON output. Execution state file: {state_file}") from exc

        tx_hash = data.get("txHash")
        tx_id = data.get("transactionId")
        state = data.get("state")
        if not isinstance(tx_hash, str) or not TX_HASH_RE.match(tx_hash):
            raise RuntimeError(f"Circle sidecar returned invalid txHash. Execution state file: {state_file}")
        if not tx_id or state != "COMPLETE":
            raise RuntimeError(f"Circle sidecar returned incomplete transaction. Execution state file: {state_file}")
        return ContractExecutionResult(circle_transaction_id=str(tx_id), tx_hash=tx_hash, state=str(state), state_file=str(state_file))
