"""SQLite spend ledger for x402 payments.

Tracks every payment attempt: pending → signed → success/failed.
Enforces daily budget and request count limits before signing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import atomic_to_usdc, load_config, usdc_to_atomic


class X402Ledger:
    def __init__(self, path: Path | None = None):
        cfg = load_config()
        self.path = path or cfg.x402_ledger_path
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS x402_spend_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    wallet_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    amount_atomic TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payment_hash TEXT,
                    tx_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_ledger_daily
                ON x402_spend_ledger(agent_key, wallet_id, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_ledger_request_id
                ON x402_spend_ledger(request_id)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_ledger_idempotent
                ON x402_spend_ledger(payment_hash)
            """)

    def check_limits_and_insert_pending(
        self, *, mode: str, agent_key: str, wallet_id: str, host: str,
        resource: str, request_id: str, amount_atomic: str,
    ) -> int:
        """Atomically check daily limits and insert pending row.

        Uses BEGIN IMMEDIATE to acquire a write lock before reading.
        Check + insert happen in one transaction — no concurrent process
        can slip between the check and the insert.
        """
        cfg = load_config()
        now = datetime.now(timezone.utc).isoformat()
        day_start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        payment_hash = hashlib.sha256(
            f"{mode}:{agent_key}:{wallet_id}:{host}:{resource}:{request_id}:{amount_atomic}".encode()
        ).hexdigest()
        max_requests = int(cfg.x402_max_requests_per_day)
        max_daily_atomic = int(usdc_to_atomic(cfg.x402_max_daily_usdc))
        amount_int = int(amount_atomic)

        conn = self._connect()
        try:
            # Acquire write lock BEFORE reading — blocks other writers
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """SELECT COUNT(*) as cnt, COALESCE(SUM(CAST(amount_atomic AS INTEGER)), 0) as total
                   FROM x402_spend_ledger
                   WHERE agent_key = ? AND wallet_id = ? AND created_at > ?
                   AND status IN ('pending', 'signed', 'success')""",
                (agent_key, wallet_id, day_start),
            ).fetchone()

            count = int(row["cnt"]) if row else 0
            total_atomic = int(row["total"]) if row else 0

            # Project AFTER adding this payment
            projected_count = count + 1
            projected_total = total_atomic + amount_int

            if projected_count > max_requests:
                conn.execute("ROLLBACK")
                raise PermissionError(
                    f"x402: daily request limit reached ({count}/{max_requests}, would be {projected_count})"
                )
            if projected_total > max_daily_atomic:
                conn.execute("ROLLBACK")
                raise PermissionError(
                    f"x402: daily budget exhausted ({total_atomic}/{max_daily_atomic} atomic, "
                    f"would be {projected_total})"
                )

            # Insert within the same transaction
            try:
                conn.execute(
                    """INSERT INTO x402_spend_ledger
                       (mode, agent_key, wallet_id, host, resource, request_id,
                        amount_atomic, status, payment_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (mode, agent_key, wallet_id, host, resource, request_id,
                     amount_atomic, payment_hash, now, now),
                )
            except sqlite3.IntegrityError:
                # payment_hash already exists — idempotent
                conn.execute("ROLLBACK")
                row = conn.execute(
                    "SELECT id FROM x402_spend_ledger WHERE payment_hash = ?",
                    (payment_hash,),
                ).fetchone()
                return int(row["id"]) if row else -1

            conn.execute("COMMIT")
            cur = conn.execute("SELECT last_insert_rowid()")
            return int(cur.fetchone()[0])
        except (PermissionError, sqlite3.IntegrityError):
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def insert_pending(
        self, *, mode: str, agent_key: str, wallet_id: str, host: str,
        resource: str, request_id: str, amount_atomic: str,
    ) -> int:
        """Insert a pending ledger row. Returns row ID."""
        now = datetime.now(timezone.utc).isoformat()
        payment_hash = hashlib.sha256(
            f"{mode}:{agent_key}:{wallet_id}:{host}:{resource}:{request_id}:{amount_atomic}".encode()
        ).hexdigest()

        with self._connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO x402_spend_ledger
                       (mode, agent_key, wallet_id, host, resource, request_id,
                        amount_atomic, status, payment_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (mode, agent_key, wallet_id, host, resource, request_id,
                     amount_atomic, payment_hash, now, now),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id FROM x402_spend_ledger WHERE payment_hash = ?",
                    (payment_hash,),
                ).fetchone()
                return int(row["id"]) if row else -1

            cur = conn.execute("SELECT last_insert_rowid()")
            return int(cur.fetchone()[0])

    def update_status(
        self, row_id: int, status: str, *, tx_hash: str | None = None,
    ) -> None:
        """Update ledger row status and optional tx_hash."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if tx_hash:
                conn.execute(
                    "UPDATE x402_spend_ledger SET status = ?, tx_hash = ?, updated_at = ? WHERE id = ?",
                    (status, tx_hash, now, row_id),
                )
            else:
                conn.execute(
                    "UPDATE x402_spend_ledger SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, row_id),
                )

    def get_daily_spend(self, agent_key: str, wallet_id: str) -> dict:
        """Return current daily spend summary."""
        now = datetime.now(timezone.utc)
        day_start = (now - timedelta(hours=24)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as cnt, COALESCE(SUM(CAST(amount_atomic AS INTEGER)), 0) as total
                   FROM x402_spend_ledger
                   WHERE agent_key = ? AND wallet_id = ? AND created_at > ?
                   AND status IN ('pending', 'signed', 'success')""",
                (agent_key, wallet_id, day_start),
            ).fetchone()

        count = int(row["cnt"]) if row else 0
        total_atomic = int(row["total"]) if row else 0
        return {
            "request_count": count,
            "total_atomic": total_atomic,
            "total_usdc": atomic_to_usdc(str(total_atomic)),
        }

    def check_already_settled(self, payment_hash: str) -> str | None:
        """Check if a payment was already settled. Returns status or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM x402_spend_ledger WHERE payment_hash = ?",
                (payment_hash,),
            ).fetchone()
        return row["status"] if row else None

    def cleanup_old_entries(self, days: int = 30) -> int:
        """Remove ledger entries older than N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM x402_spend_ledger WHERE created_at < ?", (cutoff,)
            )
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            return int(cur.rowcount or 0)
