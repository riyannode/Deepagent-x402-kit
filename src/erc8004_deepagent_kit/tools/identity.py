from __future__ import annotations

from langchain_core.tools import tool
from uuid import uuid4

from web3 import Web3

from ..config import load_config
from ..erc8004.metadata import build_registration_file, to_data_uri
from ..erc8004.receipts import IdentityReceipt
from ..erc8004.registry_clients import IdentityRegistryClient
from ..store.sqlite_store import SqliteIdentityStore
from ..wallet.contract_executor import CircleNodeSidecarExecutor
from ..wallet.dcw import get_configured_wallet
from ..wallet.policy import ContractCallIntent, WalletPolicy


def _store() -> SqliteIdentityStore:
    cfg = load_config()
    return SqliteIdentityStore(cfg.identity_store_path)


def _identity_client() -> IdentityRegistryClient:
    cfg = load_config()
    client = IdentityRegistryClient(
        cfg.rpc_url,
        cfg.identity_registry,
        cfg.from_block,
        cfg.event_scan_block_range,
        receipt_poll_seconds=cfg.receipt_poll_seconds,
        receipt_max_polls=cfg.receipt_max_polls,
    )
    if cfg.verify_chain_id:
        client.assert_chain_id(cfg.chain_id)
    client.assert_contract_code()
    return client


def _wallet_policy() -> WalletPolicy:
    cfg = load_config()
    return WalletPolicy(
        identity_registry=cfg.identity_registry,
        reputation_registry=cfg.reputation_registry,
        validation_registry=cfg.validation_registry,
        enable_reputation_writes=cfg.enable_reputation_writes,
        enable_validation_writes=cfg.enable_validation_writes,
    )


def _receipt_url(tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    cfg = load_config()
    return f"{cfg.explorer_url}/tx/{tx_hash}"


def _get_identity_status_impl(agent_key: str | None = None) -> dict:
    cfg = load_config()
    wallet = get_configured_wallet()
    key = agent_key or cfg.agent_key
    if len(key) > 128:
        raise ValueError("agent_key must be <= 128 characters")

    local = _store().find(
        chain_id=cfg.chain_id,
        identity_registry=cfg.identity_registry,
        agent_key=key,
        wallet_address=wallet.address,
    )
    if local:
        return IdentityReceipt(
            status="already_registered",
            source=local.source,
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_id=local.agent_id,
            wallet_address=wallet.address,
            agent_uri=local.agent_uri,
            tx_hash=local.tx_hash,
            explorer_url=_receipt_url(local.tx_hash),
        ).to_dict()

    onchain = _identity_client().find_registered_by_owner(wallet.address)
    if onchain:
        return IdentityReceipt(
            status="already_registered_onchain_unstored",
            source="onchain",
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_id=onchain.agent_id,
            wallet_address=wallet.address,
            agent_uri=onchain.agent_uri,
            tx_hash=onchain.tx_hash,
            explorer_url=_receipt_url(onchain.tx_hash),
            duplicate_count=onchain.duplicate_count,
        ).to_dict()

    return IdentityReceipt(
        status="unregistered",
        source="none",
        chain_id=cfg.chain_id,
        identity_registry=cfg.identity_registry,
        agent_id=None,
        wallet_address=wallet.address,
        agent_uri=None,
        tx_hash=None,
        explorer_url=None,
    ).to_dict()


def _register_identity_once_impl(
    agent_key: str | None = None,
    name: str | None = None,
    description: str | None = None,
    image: str | None = None,
) -> dict:
    cfg = load_config()
    wallet = get_configured_wallet()
    key = agent_key or cfg.agent_key
    store = _store()
    store.assert_agent_key_not_bound_to_other_wallet(
        chain_id=cfg.chain_id,
        identity_registry=cfg.identity_registry,
        agent_key=key,
        wallet_address=wallet.address,
    )

    local = store.find(
        chain_id=cfg.chain_id,
        identity_registry=cfg.identity_registry,
        agent_key=key,
        wallet_address=wallet.address,
    )
    if local:
        return IdentityReceipt(
            status="already_registered",
            source=local.source,
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_id=local.agent_id,
            wallet_address=wallet.address,
            agent_uri=local.agent_uri,
            tx_hash=local.tx_hash,
            explorer_url=_receipt_url(local.tx_hash),
        ).to_dict()

    lock_key = f"erc8004-register:{cfg.chain_id}:{cfg.identity_registry.lower()}:{wallet.address.lower()}"
    lock_owner = f"register_identity_once:{uuid4()}"
    if not store.acquire_lock(lock_key=lock_key, owner=lock_owner, ttl_seconds=cfg.registration_lock_ttl_seconds):
        raise RuntimeError("registration already in progress for this configured wallet; refusing to submit another transaction")

    release_lock = True
    try:
        # Re-check under lock to avoid double-submit from parallel Docker/process invocations.
        local = store.find(
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_key=key,
            wallet_address=wallet.address,
        )
        if local:
            return IdentityReceipt(
                status="already_registered",
                source=local.source,
                chain_id=cfg.chain_id,
                identity_registry=cfg.identity_registry,
                agent_id=local.agent_id,
                wallet_address=wallet.address,
                agent_uri=local.agent_uri,
                tx_hash=local.tx_hash,
                explorer_url=_receipt_url(local.tx_hash),
            ).to_dict()

        client = _identity_client()
        onchain = client.find_registered_by_owner(wallet.address)
        if onchain:
            if onchain.duplicate_count > 0:
                return IdentityReceipt(
                    status="blocked_duplicate_onchain_identities",
                    source="onchain",
                    chain_id=cfg.chain_id,
                    identity_registry=cfg.identity_registry,
                    agent_id=onchain.agent_id,
                    wallet_address=wallet.address,
                    agent_uri=onchain.agent_uri,
                    tx_hash=onchain.tx_hash,
                    explorer_url=_receipt_url(onchain.tx_hash),
                    duplicate_count=onchain.duplicate_count,
                ).to_dict()
            saved = store.save(
                chain_id=cfg.chain_id,
                identity_registry=cfg.identity_registry,
                agent_key=key,
                wallet_address=wallet.address,
                agent_id=onchain.agent_id,
                agent_uri=onchain.agent_uri or "",
                tx_hash=onchain.tx_hash,
                source="onchain_recovered",
            )
            return IdentityReceipt(
                status="already_registered_onchain",
                source=saved.source,
                chain_id=cfg.chain_id,
                identity_registry=cfg.identity_registry,
                agent_id=saved.agent_id,
                wallet_address=wallet.address,
                agent_uri=saved.agent_uri,
                tx_hash=saved.tx_hash,
                explorer_url=_receipt_url(saved.tx_hash),
                duplicate_count=onchain.duplicate_count,
            ).to_dict()

        registration = build_registration_file(
            name=name or cfg.agent_name,
            description=description or cfg.agent_description,
            image=image or cfg.agent_image,
            services=cfg.agent_services,
            x402_support=cfg.agent_x402_support,
            active=True,
            registrations=[],
            supported_trust=cfg.agent_supported_trust,
        )
        agent_uri = to_data_uri(registration)

        executor = CircleNodeSidecarExecutor(policy=_wallet_policy())
        # Once we attempt Circle execution, keep the lock if an exception occurs.
        # This avoids duplicate submits after sidecar timeout or ambiguous Circle/RPC failure.
        release_lock = False
        result = executor.execute(
            ContractCallIntent(
                wallet_address=wallet.address,
                blockchain=cfg.blockchain,
                contract_address=cfg.identity_registry,
                abi_function_signature="register(string)",
                abi_parameters=[agent_uri],
            )
        )

        # Recover the minted tokenId from the same transaction hash, not merely any historical mint.
        recovered = client.find_registered_in_tx(result.tx_hash, wallet.address)
        if not recovered:
            raise RuntimeError("registration tx completed but no ERC-721 Transfer mint event was found in that tx for the configured wallet")

        saved = store.save(
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_key=key,
            wallet_address=wallet.address,
            agent_id=recovered.agent_id,
            agent_uri=recovered.agent_uri or agent_uri,
            tx_hash=result.tx_hash,
            source="fresh_register",
        )

        release_lock = True
        return IdentityReceipt(
            status="registered",
            source=saved.source,
            chain_id=cfg.chain_id,
            identity_registry=cfg.identity_registry,
            agent_id=saved.agent_id,
            wallet_address=wallet.address,
            agent_uri=saved.agent_uri,
            tx_hash=result.tx_hash,
            explorer_url=_receipt_url(result.tx_hash),
            duplicate_count=0,
        ).to_dict()
    finally:
        if release_lock:
            store.release_lock(lock_key=lock_key, owner=lock_owner)


@tool
def get_identity_status(agent_key: str | None = None) -> dict:
    """Check local and on-chain ERC-8004 identity status for the configured DCW wallet."""
    return _get_identity_status_impl(agent_key=agent_key)


@tool
def register_identity_once(agent_key: str | None = None, name: str | None = None, description: str | None = None, image: str | None = None) -> dict:
    """Register exactly one ERC-8004 identity for the configured DCW wallet. Idempotent; never sends a second tx if identity already exists."""
    return _register_identity_once_impl(agent_key=agent_key, name=name, description=description, image=image)


def _parse_agent_id(agent_id: str) -> int:
    """Validate and convert agent_id string to int. Raises ValueError on bad input."""
    try:
        return int(agent_id)
    except (ValueError, TypeError):
        raise ValueError(f"agent_id must be a numeric token ID, got: {agent_id!r}")


@tool
def get_agent_metadata(agent_id: str) -> dict:
    """Read tokenURI metadata for an ERC-8004 agent ID."""
    aid = _parse_agent_id(agent_id)
    client = _identity_client()
    token_uri = client.contract.functions.tokenURI(aid).call()
    owner = client.contract.functions.ownerOf(aid).call()
    return {"agent_id": agent_id, "owner": Web3.to_checksum_address(owner), "agent_uri": token_uri}


@tool
def get_agent_wallet(agent_id: str) -> dict:
    """Read ERC-8004 agentWallet for an agent ID when the registry exposes getAgentWallet."""
    _parse_agent_id(agent_id)  # validate before calling
    wallet = _identity_client().get_agent_wallet(agent_id)
    return {"agent_id": agent_id, "agent_wallet": wallet}
