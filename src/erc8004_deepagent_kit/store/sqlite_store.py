from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from web3 import Web3

from .identity_store import StoredIdentity


class SqliteIdentityStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identities (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chain_id INTEGER NOT NULL,
                  identity_registry TEXT NOT NULL,
                  agent_key TEXT NOT NULL,
                  wallet_address TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  agent_uri TEXT NOT NULL,
                  tx_hash TEXT NOT NULL,
                  source TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_identity_once_per_agent
                ON identities(chain_id, identity_registry, agent_key)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_identity_once_per_wallet
                ON identities(chain_id, identity_registry, wallet_address)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS registration_locks (
                  lock_key TEXT PRIMARY KEY,
                  owner TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _row_to_identity(row: sqlite3.Row | None) -> StoredIdentity | None:
        if row is None:
            return None
        return StoredIdentity(
            chain_id=int(row["chain_id"]),
            identity_registry=row["identity_registry"],
            agent_key=row["agent_key"],
            wallet_address=row["wallet_address"],
            agent_id=row["agent_id"],
            agent_uri=row["agent_uri"],
            tx_hash=row["tx_hash"],
            source=row["source"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _norm_registry(identity_registry: str) -> str:
        return Web3.to_checksum_address(identity_registry).lower()

    @staticmethod
    def _norm_wallet(wallet_address: str) -> str:
        return Web3.to_checksum_address(wallet_address).lower()

    def find_by_wallet(self, *, chain_id: int, identity_registry: str, wallet_address: str) -> StoredIdentity | None:
        identity_registry = self._norm_registry(identity_registry)
        wallet_address = self._norm_wallet(wallet_address)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM identities
                WHERE chain_id = ? AND identity_registry = ? AND wallet_address = ?
                ORDER BY id ASC LIMIT 1
                """,
                (chain_id, identity_registry, wallet_address),
            ).fetchone()
        return self._row_to_identity(row)

    def find_by_agent_key(self, *, chain_id: int, identity_registry: str, agent_key: str) -> StoredIdentity | None:
        identity_registry = self._norm_registry(identity_registry)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM identities
                WHERE chain_id = ? AND identity_registry = ? AND agent_key = ?
                ORDER BY id ASC LIMIT 1
                """,
                (chain_id, identity_registry, agent_key),
            ).fetchone()
        return self._row_to_identity(row)

    def find(self, *, chain_id: int, identity_registry: str, agent_key: str, wallet_address: str) -> StoredIdentity | None:
        by_wallet = self.find_by_wallet(chain_id=chain_id, identity_registry=identity_registry, wallet_address=wallet_address)
        if by_wallet:
            return by_wallet
        return self.find_by_agent_key(chain_id=chain_id, identity_registry=identity_registry, agent_key=agent_key)

    def assert_agent_key_not_bound_to_other_wallet(self, *, chain_id: int, identity_registry: str, agent_key: str, wallet_address: str) -> None:
        existing = self.find_by_agent_key(chain_id=chain_id, identity_registry=identity_registry, agent_key=agent_key)
        if existing and existing.wallet_address.lower() != self._norm_wallet(wallet_address):
            raise RuntimeError(
                "AGENT_KEY is already bound to a different wallet in the local identity store. "
                "Use the original DCW wallet, change AGENT_KEY, or intentionally rotate identity outside this SDK."
            )

    def acquire_lock(self, *, lock_key: str, owner: str, ttl_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=max(300, ttl_seconds))).isoformat()
        now_s = now.isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM registration_locks WHERE expires_at < ?", (now_s,))
            try:
                conn.execute(
                    "INSERT INTO registration_locks(lock_key, owner, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (lock_key, owner, expires_at, now_s),
                )
                conn.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return False

    def release_lock(self, *, lock_key: str, owner: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM registration_locks WHERE lock_key = ? AND owner = ?", (lock_key, owner))

    def clear_expired_locks(self) -> int:
        now_s = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM registration_locks WHERE expires_at < ?", (now_s,))
            # L4: Periodic WAL checkpoint to prevent unbounded WAL growth
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass  # checkpoint is best-effort
            return int(cur.rowcount or 0)

    def save(self, *, chain_id: int, identity_registry: str, agent_key: str, wallet_address: str, agent_id: str, agent_uri: str, tx_hash: str, source: str) -> StoredIdentity:
        identity_registry = self._norm_registry(identity_registry)
        wallet_address = self._norm_wallet(wallet_address)
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO identities(chain_id, identity_registry, agent_key, wallet_address, agent_id, agent_uri, tx_hash, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chain_id, identity_registry, agent_key, wallet_address, str(agent_id), agent_uri, tx_hash, source, created_at),
                )
        except sqlite3.IntegrityError:
            existing_wallet = self.find_by_wallet(chain_id=chain_id, identity_registry=identity_registry, wallet_address=wallet_address)
            if existing_wallet:
                return existing_wallet
            existing_agent = self.find_by_agent_key(chain_id=chain_id, identity_registry=identity_registry, agent_key=agent_key)
            if existing_agent:
                return existing_agent
            raise
        return StoredIdentity(chain_id, identity_registry, agent_key, wallet_address, str(agent_id), agent_uri, tx_hash, source, created_at)
