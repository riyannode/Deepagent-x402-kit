from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from web3 import Web3

from .config import _is_placeholder_secret, load_config
from .erc8004.registry_clients import IdentityRegistryClient, ReputationRegistryClient
from .store.sqlite_store import SqliteIdentityStore
from .erc8004.reputation_indexer import ReputationIndexer
from .store.reputation_store import ReputationStore
from .tools.identity import _get_identity_status_impl, _register_identity_once_impl
from .tools.registry_status import _get_erc8004_config_impl

app = typer.Typer(no_args_is_help=False, add_completion=False)


def _print(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, sort_keys=True))



def _check_writable(path: Path) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    test_file = path / ".doctor_write_test"
    test_file.write_text("ok")
    test_file.unlink()
    return {"ok": True, "path": str(path)}

def _safe_check(name: str, fn, *, required: bool = True) -> dict:
    try:
        value = fn()
        if isinstance(value, dict):
            return {"name": name, "ok": bool(value.get("ok", True)), **value, "required": required}
        return {"name": name, "ok": bool(value), "value": value, "required": required}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc), "required": required}


@app.command()
def config() -> None:
    """Print safe ERC-8004 registry config. No secrets."""
    _print(_get_erc8004_config_impl())


@app.command()
def doctor() -> None:
    """Validate live Docker/env/RPC configuration without sending a transaction."""
    cfg = load_config()
    client = IdentityRegistryClient(
        cfg.rpc_url,
        cfg.identity_registry,
        cfg.from_block,
        cfg.event_scan_block_range,
        receipt_poll_seconds=cfg.receipt_poll_seconds,
        receipt_max_polls=cfg.receipt_max_polls,
    )

    reputation_client = ReputationRegistryClient(cfg.rpc_url, cfg.reputation_registry)

    checks = [
        {"name": "execution_mode", "ok": True, "value": "live_circle_only", "required": True},
        {"name": "identity_registry_address", "ok": Web3.is_address(cfg.identity_registry), "value": cfg.identity_registry, "required": True},
        {"name": "reputation_registry_address", "ok": Web3.is_address(cfg.reputation_registry), "value": cfg.reputation_registry, "required": True},
        {"name": "validation_registry_address", "ok": Web3.is_address(cfg.validation_registry), "value": cfg.validation_registry, "required": True},
        {"name": "dcw_wallet_address_present", "ok": bool(cfg.dcw_wallet_address), "required": True},
        {"name": "circle_api_key_present", "ok": bool(cfg.circle_api_key), "required": True},
        {"name": "circle_entity_secret_present", "ok": bool(cfg.circle_entity_secret), "required": True},
        {"name": "anthropic_api_key_present", "ok": not _is_placeholder_secret(os.getenv("ANTHROPIC_API_KEY")), "required": True},
        {"name": "circle_credentials_live_checked", "ok": True, "value": False, "note": "presence checked only; run a live DCW wallet/status check before production", "required": False},
        {"name": "identity_store_parent_exists", "ok": cfg.identity_store_path.parent.exists() or cfg.identity_store_path.parent == Path('/data'), "value": str(cfg.identity_store_path), "required": True},
        {"name": "circle_state_dir_parent_exists", "ok": cfg.circle_execution_state_dir.parent.exists() or cfg.circle_execution_state_dir.parent == Path('/data'), "value": str(cfg.circle_execution_state_dir), "required": True},
        _safe_check("rpc_chain_id", lambda: {"ok": int(client.w3.eth.chain_id) == cfg.chain_id, "value": int(client.w3.eth.chain_id), "expected": cfg.chain_id}),
        _safe_check("identity_registry_bytecode", lambda: {"ok": client.contract_code_size() > 0, "bytes": client.contract_code_size()}),
        _safe_check("reputation_registry_bytecode", lambda: {"ok": reputation_client.contract_code_size() > 0, "bytes": reputation_client.contract_code_size()}),
        _safe_check("reputation_store_parent_writable", lambda: _check_writable(cfg.reputation_store_path.parent)),
        _safe_check("latest_block", lambda: {"ok": client.w3.eth.block_number >= cfg.from_block, "value": int(client.w3.eth.block_number), "from_block": cfg.from_block}),
    ]

    if cfg.enable_reputation_writes:
        checks.append({"name": "reputation_writer_wallet_address_present", "ok": bool(cfg.reputation_writer_wallet_address), "required": True})
        checks.append({"name": "reputation_writer_not_identity_wallet", "ok": bool(cfg.reputation_writer_wallet_address and cfg.dcw_wallet_address and Web3.to_checksum_address(cfg.reputation_writer_wallet_address) != Web3.to_checksum_address(cfg.dcw_wallet_address)), "required": True})

    # ── x402 checks ─────────────────────────────────────────────
    if cfg.x402_enabled:
        sdk_root = os.getenv("SDK_PROJECT_ROOT", "/app")

        # Sidecar files exist
        checks.append(_safe_check(
            "x402_batching_sidecar_exists",
            lambda: {"ok": (Path(sdk_root) / "scripts" / "x402_batching.mjs").is_file()},
        ))
        checks.append(_safe_check(
            "x402_nano_sidecar_exists",
            lambda: {"ok": (Path(sdk_root) / "scripts" / "x402_nano.mjs").is_file()},
        ))

        # If batching mode, check @circle-fin/x402-batching is importable
        if cfg.x402_mode == "batching":
            def _check_batching_pkg():
                node = shutil.which("node")
                if not node:
                    return {"ok": False, "error": "node not found on PATH"}
                r = subprocess.run(
                    [node, "-e", "require('@circle-fin/x402-batching/server')"],
                    capture_output=True, text=True, timeout=15,
                )
                return {"ok": r.returncode == 0, "error": r.stderr[:200] if r.returncode != 0 else None}
            checks.append(_safe_check("x402_batching_package_importable", _check_batching_pkg))

        # Gateway API URL configured
        checks.append({
            "name": "x402_gateway_api_url",
            "ok": bool(cfg.x402_gateway_api_url),
            "value": cfg.x402_gateway_api_url or "",
            "required": True,
        })

        # Ledger path writable
        def _check_ledger_writable():
            ledger_dir = cfg.x402_ledger_path.parent
            ledger_dir.mkdir(parents=True, exist_ok=True)
            test_file = ledger_dir / ".doctor_write_test"
            test_file.write_text("ok")
            test_file.unlink()
            return {"ok": True, "path": str(ledger_dir)}
        checks.append(_safe_check("x402_ledger_path_writable", _check_ledger_writable))

    # Buyer exposure checks
    buyer_exposed = cfg.x402_expose_batch_buyer_to_agent or cfg.x402_expose_nano_buyer_to_agent
    if buyer_exposed:
        checks.append({
            "name": "x402_buyer_wallet_id_required",
            "ok": bool(cfg.x402_default_buyer_wallet_id),
            "required": True,
        })
        checks.append({
            "name": "x402_allowed_hosts_required",
            "ok": bool(cfg.x402_allowed_hosts and cfg.x402_allowed_hosts.strip()),
            "required": True,
        })

    # Seller exposure checks
    seller_exposed = cfg.x402_expose_batch_seller_to_agent or cfg.x402_expose_nano_seller_to_agent
    if seller_exposed:
        checks.append({
            "name": "x402_seller_wallet_address_required",
            "ok": bool(cfg.x402_default_seller_wallet_address),
            "required": True,
        })

    ok = all(bool(c.get("ok")) for c in checks if c.get("required", True))
    _print({"ok": ok, "mode": "live_circle_only", "checks": checks, "sends_transaction": False})
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Check whether the configured DCW wallet already has an SDK-managed or on-chain ERC-8004 identity."""
    _print(_get_identity_status_impl())


@app.command()
def register(
    agent_key: Optional[str] = typer.Option(None, help="Stable developer-defined agent key."),
    name: Optional[str] = typer.Option(None, help="Agent display name."),
    description: Optional[str] = typer.Option(None, help="Agent description."),
    image: Optional[str] = typer.Option(None, help="Agent image URL."),
) -> None:
    """Register exactly one ERC-8004 identity for the configured Circle DCW wallet."""
    _print(
        _register_identity_once_impl(
            agent_key=agent_key,
            name=name,
            description=description,
            image=image,
        )
    )


@app.command("clear-expired-locks")
def clear_expired_locks() -> None:
    """Clear only expired local registration locks. This never sends a transaction."""
    cfg = load_config()
    store = SqliteIdentityStore(cfg.identity_store_path)
    cleared = store.clear_expired_locks()
    _print({"ok": True, "cleared": cleared, "sends_transaction": False})


@app.command("agent-register")
def agent_register() -> None:
    """Ask the LangChain Deep Agent to register identity once using bounded tools."""
    cfg = load_config()
    from .agent import build_erc8004_deep_agent

    agent = build_erc8004_deep_agent()
    result = agent.invoke(
        {
            "messages": (
                "Register this ERC-8004 agent identity if it does not already exist. "
                f"Use agent_key={cfg.agent_key!r}, name={cfg.agent_name!r}. "
                "Return the structured receipt from the tool."
            )
        }
    )
    _print(result)


def _reputation_indexer() -> ReputationIndexer:
    cfg = load_config()
    return ReputationIndexer(cfg.rpc_url, cfg.reputation_registry, ReputationStore(cfg.reputation_store_path, cfg.chain_id, cfg.reputation_registry), cfg.reputation_indexer_from_block, cfg.reputation_indexer_block_range)


@app.command("reputation-index-once")
def reputation_index_once(to_block: Optional[int] = typer.Option(None)) -> None:
    """Index ERC-8004 reputation events once. Read-only RPC + SQLite write."""
    _print(_reputation_indexer().index_once(to_block))


@app.command("reputation-index-range")
def reputation_index_range(from_block: int = typer.Option(..., "--from-block"), to_block: int = typer.Option(..., "--to-block")) -> None:
    """Index ERC-8004 reputation events for an explicit block range."""
    _print(_reputation_indexer().index_range(from_block, to_block))


@app.command("reputation-index-status")
def reputation_index_status() -> None:
    """Show local ERC-8004 reputation indexer status."""
    cfg = load_config()
    store = ReputationStore(cfg.reputation_store_path, cfg.chain_id, cfg.reputation_registry)
    latest = None
    try:
        latest = int(ReputationRegistryClient(cfg.rpc_url, cfg.reputation_registry).w3.eth.block_number)
    except Exception:
        latest = None
    _print(store.status(latest))


@app.command("reputation-read-feedback")
def reputation_read_feedback(agent_id: str = typer.Option(..., "--agent-id"), client_address: str = typer.Option(..., "--client-address"), feedback_index: int = typer.Option(..., "--feedback-index")) -> None:
    """Read one ERC-8004 reputation feedback from chain and indexed metadata."""
    from .tools.reputation import read_reputation_feedback
    _print(read_reputation_feedback.invoke({"agent_id": agent_id, "client_address": client_address, "feedback_index": feedback_index}))


if __name__ == "__main__":
    app()
