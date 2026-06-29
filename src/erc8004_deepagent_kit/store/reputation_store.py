from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ReputationStore:
    def __init__(self, path: Path, chain_id: int | str, registry: str):
        if chain_id is None or str(chain_id).strip() == "":
            raise ValueError("chain_id is required for ReputationStore scope")
        if not registry or not str(registry).strip():
            raise ValueError("registry is required for ReputationStore scope")
        base_path = Path(path)
        suffix = f"chain{chain_id}-{str(registry).lower()}"
        base_path = base_path.with_name(f"{base_path.stem}-{suffix}{base_path.suffix}")
        self.path = base_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        return conn

    def _init(self) -> None:
        with self._conn() as db:
            db.executescript(SCHEMA)
            for statement in (
                "ALTER TABLE reputation_feedback ADD COLUMN revoked_tx_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE reputation_feedback ADD COLUMN revoked_block_number INTEGER",
                "ALTER TABLE reputation_feedback ADD COLUMN revoked_log_index INTEGER",
                "ALTER TABLE reputation_feedback ADD COLUMN revoked_at TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    db.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    def upsert_feedback(self, row: dict) -> None:
        data = dict(row)
        data.setdefault("created_at", _now())
        with self._conn() as db:
            db.execute(
                """INSERT INTO reputation_feedback(agent_id,client_address,feedback_index,value,value_decimals,tag1,tag2,endpoint,feedback_uri,feedback_hash,is_revoked,tx_hash,block_number,log_index,created_at)
                VALUES(:agent_id,:client_address,:feedback_index,:value,:value_decimals,:tag1,:tag2,:endpoint,:feedback_uri,:feedback_hash,:is_revoked,:tx_hash,:block_number,:log_index,:created_at)
                ON CONFLICT(agent_id,client_address,feedback_index) DO UPDATE SET value=excluded.value,value_decimals=excluded.value_decimals,tag1=excluded.tag1,tag2=excluded.tag2,endpoint=excluded.endpoint,feedback_uri=excluded.feedback_uri,feedback_hash=excluded.feedback_hash,tx_hash=excluded.tx_hash,block_number=excluded.block_number,log_index=excluded.log_index""",
                data,
            )

    def mark_revoked(self, agent_id: str, client_address: str, feedback_index: int, tx_hash: str, block_number: int, log_index: int) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE reputation_feedback SET is_revoked=1, revoked_tx_hash=?, revoked_block_number=?, revoked_log_index=?, revoked_at=? WHERE agent_id=? AND client_address=? AND feedback_index=?",
                (tx_hash, int(block_number), int(log_index), _now(), str(agent_id), client_address, int(feedback_index)),
            )

    def insert_response(self, row: dict) -> bool:
        data = dict(row)
        data.setdefault("created_at", _now())
        with self._conn() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO reputation_responses(agent_id,client_address,feedback_index,responder,response_uri,response_hash,tx_hash,block_number,log_index,created_at)
                VALUES(:agent_id,:client_address,:feedback_index,:responder,:response_uri,:response_hash,:tx_hash,:block_number,:log_index,:created_at)""",
                data,
            )
            return cursor.rowcount == 1

    def list_feedback(self, agent_id: str, client_addresses: list[str] | None = None, tag1: str = "", tag2: str = "", include_revoked: bool = False, limit: int = 50, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM reputation_feedback WHERE agent_id=?"
        args: list = [str(agent_id)]
        if client_addresses:
            sql += " AND client_address IN (%s)" % ",".join("?" for _ in client_addresses)
            args.extend(client_addresses)
        if tag1:
            sql += " AND tag1=?"; args.append(tag1)
        if tag2:
            sql += " AND tag2=?"; args.append(tag2)
        if not include_revoked:
            sql += " AND is_revoked=0"
        sql += " ORDER BY block_number DESC, log_index DESC LIMIT ? OFFSET ?"
        args.extend([max(0, int(limit)), max(0, int(offset))])
        with self._conn() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def get_feedback(self, agent_id: str, client_address: str, feedback_index: int) -> dict | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM reputation_feedback WHERE agent_id=? AND client_address=? AND feedback_index=?", (str(agent_id), client_address, int(feedback_index))).fetchone()
            return dict(row) if row else None

    def list_clients(self, agent_id: str) -> list[str]:
        with self._conn() as db:
            return [r[0] for r in db.execute("SELECT DISTINCT client_address FROM reputation_feedback WHERE agent_id=? ORDER BY client_address", (str(agent_id),)).fetchall()]

    def get_responses(self, agent_id: str, client_address: str, feedback_index: int) -> list[dict]:
        with self._conn() as db:
            return [dict(r) for r in db.execute("SELECT * FROM reputation_responses WHERE agent_id=? AND client_address=? AND feedback_index=? ORDER BY block_number, log_index", (str(agent_id), client_address, int(feedback_index))).fetchall()]

    def get_state(self, name: str) -> int | None:
        with self._conn() as db:
            row = db.execute("SELECT last_block FROM erc8004_indexer_state WHERE name=?", (name,)).fetchone()
            return int(row[0]) if row else None

    def set_state(self, name: str, last_block: int) -> None:
        with self._conn() as db:
            db.execute("INSERT INTO erc8004_indexer_state(name,last_block,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET last_block=excluded.last_block, updated_at=excluded.updated_at", (name, int(last_block), _now()))

    def advance_state(self, name: str, last_block: int) -> None:
        with self._conn() as db:
            db.execute(
                """INSERT INTO erc8004_indexer_state(name,last_block,updated_at) VALUES(?,?,?)
                ON CONFLICT(name) DO UPDATE SET last_block=max(erc8004_indexer_state.last_block, excluded.last_block), updated_at=excluded.updated_at""",
                (name, int(last_block), _now()),
            )

    def status(self, latest_block: int | None = None) -> dict:
        last = self.get_state("reputation")
        out = {"ok": True, "state": "ready" if last is not None else "indexer_required", "last_indexed_block": last, "feedback_available": last is not None, "store_path": str(self.path)}
        if latest_block is not None:
            out["latest_block"] = int(latest_block); out["blocks_behind"] = None if last is None else max(0, int(latest_block) - last)
        return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS reputation_feedback (agent_id TEXT NOT NULL, client_address TEXT NOT NULL, feedback_index INTEGER NOT NULL, value TEXT NOT NULL, value_decimals INTEGER NOT NULL, tag1 TEXT NOT NULL DEFAULT '', tag2 TEXT NOT NULL DEFAULT '', endpoint TEXT NOT NULL DEFAULT '', feedback_uri TEXT NOT NULL DEFAULT '', feedback_hash TEXT NOT NULL DEFAULT '', is_revoked INTEGER NOT NULL DEFAULT 0, tx_hash TEXT NOT NULL, block_number INTEGER NOT NULL, log_index INTEGER NOT NULL, created_at TEXT NOT NULL, revoked_tx_hash TEXT NOT NULL DEFAULT '', revoked_block_number INTEGER, revoked_log_index INTEGER, revoked_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (agent_id, client_address, feedback_index));
CREATE TABLE IF NOT EXISTS reputation_responses (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, client_address TEXT NOT NULL, feedback_index INTEGER NOT NULL, responder TEXT NOT NULL, response_uri TEXT NOT NULL DEFAULT '', response_hash TEXT NOT NULL DEFAULT '', tx_hash TEXT NOT NULL, block_number INTEGER NOT NULL, log_index INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(tx_hash, log_index));
CREATE TABLE IF NOT EXISTS erc8004_indexer_state (name TEXT PRIMARY KEY, last_block INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reputation_agent_block ON reputation_feedback(agent_id, block_number, log_index);
CREATE INDEX IF NOT EXISTS idx_reputation_agent_tag ON reputation_feedback(agent_id, tag1, tag2);
CREATE INDEX IF NOT EXISTS idx_reputation_client ON reputation_feedback(client_address);
DELETE FROM reputation_responses WHERE rowid NOT IN (SELECT min(rowid) FROM reputation_responses GROUP BY tx_hash, log_index);
CREATE INDEX IF NOT EXISTS idx_reputation_responses_lookup ON reputation_responses(agent_id, client_address, feedback_index);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reputation_responses_event ON reputation_responses(tx_hash, log_index);
"""
