from __future__ import annotations

from web3 import Web3

from .abi_reputation import REPUTATION_REGISTRY_ABI
from ..store.reputation_store import ReputationStore

STATE_KEY = "reputation"


def _hex(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return Web3.to_hex(value)
    return str(value)


class ReputationIndexer:
    def __init__(self, rpc_url: str, reputation_registry: str, store: ReputationStore, from_block: int, block_range: int = 10000):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.registry = Web3.to_checksum_address(reputation_registry)
        self.store = store
        self.from_block = max(0, int(from_block))
        self.block_range = max(1, min(int(block_range), 10000))
        self.contract = self.w3.eth.contract(address=self.registry, abi=REPUTATION_REGISTRY_ABI)

    def assert_contract_code(self) -> None:
        code = self.w3.eth.get_code(self.registry)
        if not code or code == b"":
            raise RuntimeError(f"no contract bytecode found at ReputationRegistry address: {self.registry}")

    def index_once(self, to_block: int | None = None) -> dict:
        self.assert_contract_code()
        latest = int(self.w3.eth.block_number if to_block is None else to_block)
        start = self.store.get_state(STATE_KEY)
        from_block = self.from_block if start is None else start + 1
        if from_block > latest:
            return {"ok": True, "from_block": from_block, "to_block": latest, "latest_block": latest, "chunks": 0, "new_feedback": 0, "revoked": 0, "responses": 0, "last_indexed_block": start}
        return self._index_range(from_block, latest, advance_state=True)

    def index_range(self, from_block: int, to_block: int) -> dict:
        self.assert_contract_code()
        start = max(0, int(from_block))
        current = self.store.get_state(STATE_KEY)
        contiguous = (current is None and start == self.from_block) or (current is not None and start == current + 1)
        return self._index_range(from_block, to_block, advance_state=contiguous)

    def _index_range(self, from_block: int, to_block: int, *, advance_state: bool) -> dict:
        start = max(0, int(from_block)); end = int(to_block)
        if end < start:
            raise ValueError("to_block must be >= from_block")
        counts = {"chunks": 0, "new_feedback": 0, "revoked": 0, "responses": 0}
        cursor = start
        while cursor <= end:
            chunk_to = min(end, cursor + self.block_range - 1)
            new_logs = self.contract.events.NewFeedback().get_logs(from_block=cursor, to_block=chunk_to)
            revoked_logs = self.contract.events.FeedbackRevoked().get_logs(from_block=cursor, to_block=chunk_to)
            response_logs = self.contract.events.ResponseAppended().get_logs(from_block=cursor, to_block=chunk_to)
            for event in new_logs:
                a = event["args"]
                self.store.upsert_feedback({
                    "agent_id": str(a["agentId"]), "client_address": Web3.to_checksum_address(a["clientAddress"]), "feedback_index": int(a["feedbackIndex"]),
                    "value": str(a["value"]), "value_decimals": int(a["valueDecimals"]), "tag1": a.get("tag1", ""), "tag2": a.get("tag2", ""), "endpoint": a.get("endpoint", ""), "feedback_uri": a.get("feedbackURI", ""), "feedback_hash": _hex(a.get("feedbackHash", "")), "is_revoked": 0,
                    "tx_hash": Web3.to_hex(event["transactionHash"]), "block_number": int(event["blockNumber"]), "log_index": int(event["logIndex"]),
                })
                counts["new_feedback"] += 1
            for event in revoked_logs:
                a = event["args"]
                self.store.mark_revoked(str(a["agentId"]), Web3.to_checksum_address(a["clientAddress"]), int(a["feedbackIndex"]), Web3.to_hex(event["transactionHash"]), int(event["blockNumber"]), int(event["logIndex"]))
                counts["revoked"] += 1
            for event in response_logs:
                a = event["args"]
                inserted = self.store.insert_response({"agent_id": str(a["agentId"]), "client_address": Web3.to_checksum_address(a["clientAddress"]), "feedback_index": int(a["feedbackIndex"]), "responder": Web3.to_checksum_address(a["responder"]), "response_uri": a.get("responseURI", ""), "response_hash": _hex(a.get("responseHash", "")), "tx_hash": Web3.to_hex(event["transactionHash"]), "block_number": int(event["blockNumber"]), "log_index": int(event["logIndex"])})
                if inserted:
                    counts["responses"] += 1
            if advance_state:
                self.store.advance_state(STATE_KEY, chunk_to)
            counts["chunks"] += 1
            cursor = chunk_to + 1
        return {"ok": True, "from_block": start, "to_block": end, "latest_block": end, **counts, "advanced_state": advance_state, "last_indexed_block": self.store.get_state(STATE_KEY)}
