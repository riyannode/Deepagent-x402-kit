from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.exceptions import ContractLogicError

from .abi_identity import IDENTITY_REGISTRY_ABI
from .abi_validation import VALIDATION_REGISTRY_ABI
from .events import TRANSFER_TOPIC, ZERO_ADDRESS, MintEvent, address_topic, int_from_topic


@dataclass(frozen=True)
class OnchainIdentity:
    agent_id: str
    wallet_address: str
    agent_uri: str | None
    tx_hash: str
    block_number: int
    log_index: int
    duplicate_count: int = 0


class IdentityRegistryClient:
    def __init__(self, rpc_url: str, registry: str, from_block: int = 0, block_range: int = 10000, receipt_poll_seconds: int = 3, receipt_max_polls: int = 60):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.registry = Web3.to_checksum_address(registry)
        self.from_block = max(0, int(from_block))
        self.block_range = max(1, min(int(block_range), 10000))
        self.receipt_poll_seconds = max(1, int(receipt_poll_seconds))
        self.receipt_max_polls = max(1, int(receipt_max_polls))
        self.contract = self.w3.eth.contract(address=self.registry, abi=IDENTITY_REGISTRY_ABI)

    def assert_chain_id(self, expected_chain_id: int) -> None:
        actual = int(self.w3.eth.chain_id)
        if actual != int(expected_chain_id):
            raise RuntimeError(f"RPC chain id mismatch: expected {expected_chain_id}, got {actual}")

    def assert_contract_code(self) -> None:
        code = self.w3.eth.get_code(self.registry)
        if not code or code == b"":
            raise RuntimeError(f"no contract bytecode found at IdentityRegistry address: {self.registry}")

    def contract_code_size(self) -> int:
        return len(self.w3.eth.get_code(self.registry))

    def _latest_block(self) -> int:
        return int(self.w3.eth.block_number)

    def _token_uri(self, agent_id: str) -> str | None:
        try:
            return self.contract.functions.tokenURI(int(agent_id)).call()
        except (ContractLogicError, ValueError):
            return None

    def _identity_from_event(self, event: MintEvent, *, duplicate_count: int = 0) -> OnchainIdentity:
        return OnchainIdentity(
            agent_id=event.agent_id,
            wallet_address=event.owner,
            agent_uri=self._token_uri(event.agent_id),
            tx_hash=event.tx_hash,
            block_number=event.block_number,
            log_index=event.log_index,
            duplicate_count=duplicate_count,
        )

    def _balance_of(self, owner: str) -> int:
        """Fast check: how many identity tokens does this wallet own?"""
        try:
            return int(self.contract.functions.balanceOf(Web3.to_checksum_address(owner)).call())
        except (ContractLogicError, ValueError):
            return -1

    def find_registered_by_owner(self, owner: str) -> OnchainIdentity | None:
        owner = Web3.to_checksum_address(owner)
        latest = self._latest_block()
        events: list[MintEvent] = []
        from_block = self.from_block
        to_topic = address_topic(owner)
        zero_topic = address_topic(ZERO_ADDRESS)

        # Fast path: if wallet currently owns tokens, we definitely need to scan.
        # If balance==0, the NFT may have been transferred out — still scan recent
        # range to catch historical mints (duplicate prevention).
        balance = self._balance_of(owner)
        if balance == -1:
            # balanceOf call failed (contract error) — proceed with full scan
            pass
        elif balance == 0:
            # No current ownership, but scan recent range to catch historical mints
            # Use a narrower window (last 100k blocks) for balance==0 case
            recent_range = min(self.block_range * 10, 100000)
            from_block = max(from_block, latest - recent_range)

        if from_block > latest:
            return None

        # Auto-narrow scan range: start from recent blocks if from_block is too old
        max_scan_blocks = self.block_range * 50  # max 500k blocks
        if latest - from_block > max_scan_blocks:
            from_block = latest - max_scan_blocks

        while from_block <= latest:
            to_block = min(latest, from_block + self.block_range - 1)
            logs = self.w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": self.registry,
                    "topics": [TRANSFER_TOPIC, zero_topic, to_topic],
                }
            )
            for log in logs:
                token_id = int_from_topic(Web3.to_hex(log["topics"][3]))
                events.append(
                    MintEvent(
                        agent_id=str(token_id),
                        owner=owner,
                        tx_hash=Web3.to_hex(log["transactionHash"]),
                        block_number=int(log["blockNumber"]),
                        log_index=int(log["logIndex"]),
                    )
                )
            from_block = to_block + 1

        if not events:
            return None

        events.sort(key=lambda e: (e.block_number, e.log_index))
        return self._identity_from_event(events[0], duplicate_count=max(0, len(events) - 1))

    def _wait_receipt(self, tx_hash: str):
        last_exc: Exception | None = None
        for _ in range(self.receipt_max_polls):
            try:
                return self.w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound as exc:
                last_exc = exc
                time.sleep(self.receipt_poll_seconds)
        raise RuntimeError(f"transaction receipt not found after polling: {tx_hash}") from last_exc

    def find_registered_in_tx(self, tx_hash: str, owner: str) -> OnchainIdentity | None:
        if not tx_hash.startswith("0x") or len(tx_hash) != 66:
            raise ValueError("tx_hash must be a 32-byte hex transaction hash")
        owner = Web3.to_checksum_address(owner)
        receipt = self._wait_receipt(tx_hash)
        if int(receipt.get("status", 1)) != 1:
            raise RuntimeError(f"registration transaction reverted: {tx_hash}")
        zero_topic = address_topic(ZERO_ADDRESS).lower()
        to_topic = address_topic(owner).lower()
        events: list[MintEvent] = []
        for log in receipt.get("logs", []):
            if Web3.to_checksum_address(log["address"]) != self.registry:
                continue
            topics = [Web3.to_hex(t).lower() for t in log["topics"]]
            if len(topics) < 4:
                continue
            if topics[0] == TRANSFER_TOPIC.lower() and topics[1] == zero_topic and topics[2] == to_topic:
                events.append(
                    MintEvent(
                        agent_id=str(int_from_topic(topics[3])),
                        owner=owner,
                        tx_hash=tx_hash,
                        block_number=int(log["blockNumber"]),
                        log_index=int(log["logIndex"]),
                    )
                )
        if not events:
            return None
        events.sort(key=lambda e: (e.block_number, e.log_index))
        return self._identity_from_event(events[0])

    def get_agent_wallet(self, agent_id: str) -> str | None:
        try:
            return self.contract.functions.getAgentWallet(int(agent_id)).call()
        except (ContractLogicError, ValueError):
            return None


class ValidationRegistryClient:
    def __init__(self, rpc_url: str, registry: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.registry = Web3.to_checksum_address(registry)
        self.contract = self.w3.eth.contract(address=self.registry, abi=VALIDATION_REGISTRY_ABI)

    def get_validation_status(self, request_hash: str) -> dict[str, Any]:
        if not request_hash.startswith("0x") or len(request_hash) != 66:
            raise ValueError("request_hash must be bytes32 hex")
        out = self.contract.functions.getValidationStatus(request_hash).call()
        return {
            "validator_address": out[0],
            "agent_id": str(out[1]),
            "response": int(out[2]),
            "response_hash": out[3].hex() if isinstance(out[3], bytes) else out[3],
            "tag": out[4],
            "last_update": int(out[5]),
        }
