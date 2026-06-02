"""SQLite WAL state store and async change notifier for file watch."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite


logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fw_watchers (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fw_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher_id TEXT NOT NULL,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    relative_path TEXT,
    old_path TEXT,
    is_dir INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL,
    mtime REAL,
    size INTEGER,
    inode INTEGER,
    FOREIGN KEY (watcher_id) REFERENCES fw_watchers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fw_events_watcher_seq
    ON fw_events(watcher_id, seq);
"""


class ChangeNotifier:
    """Per-key asyncio.Condition + version counter for change notification."""

    def __init__(self) -> None:
        self._conditions: dict[str, asyncio.Condition] = {}
        self._versions: dict[str, int] = {}

    def _ensure_key(self, key: str) -> None:
        if key not in self._conditions:
            self._conditions[key] = asyncio.Condition()
        if key not in self._versions:
            self._versions[key] = 0

    def notify(self, key: str) -> None:
        """Increment version and wake all waiters for *key*. Noop if key unknown."""
        if key not in self._conditions:
            # No waiters yet — just track the version
            self._versions[key] = self._versions.get(key, 0) + 1
            return
        self._versions[key] += 1
        cond = self._conditions[key]
        # Schedule the notify_all inside the condition lock
        asyncio.ensure_future(self._do_notify(cond))

    async def _do_notify(self, cond: asyncio.Condition) -> None:
        async with cond:
            cond.notify_all()

    async def wait(self, key: str, last_version: int) -> int:
        """Block until version for *key* exceeds *last_version*. Returns new version."""
        self._ensure_key(key)
        cond = self._conditions[key]
        async with cond:
            while self._versions.get(key, 0) <= last_version:
                await cond.wait()
            return self._versions[key]

    def get_version(self, key: str) -> int:
        """Return the current version for *key*, creating tracking state if needed."""
        self._ensure_key(key)
        return self._versions[key]

    def notify_all(self) -> None:
        """Wake all waiters on all keys."""
        for key in list(self._conditions):
            self._versions[key] = self._versions.get(key, 0) + 1
            asyncio.ensure_future(self._do_notify(self._conditions[key]))

    def remove(self, key: str) -> None:
        """Remove key to prevent memory leaks."""
        self._conditions.pop(key, None)
        self._versions.pop(key, None)


class StateStore:
    """Async SQLite WAL state store for file-watch watchers and events."""

    def __init__(
        self,
        db_path: str | None = None,
        buffer_size: int | None = None,
    ) -> None:
        self._db_path = db_path or os.getenv(
            "AIO_STATE_DB_PATH", "/tmp/aio-sandbox/state.db"
        )
        self._buffer_size = buffer_size or int(
            os.getenv("AIO_FILE_WATCH_BUFFER_SIZE", "10000")
        )
        self._insert_count = 0
        self._initialized = False
        self.notifier = ChangeNotifier()

    @asynccontextmanager
    async def _db_connection(self):
        """Yield an aiosqlite connection with WAL pragmas."""
        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        if self._initialized:
            return
        # Ensure directory exists
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        async with self._db_connection() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(_SCHEMA_SQL)
            await self._ensure_event_columns(db)
            await db.commit()
        self._initialized = True
        logger.info("StateStore initialized at %s", self._db_path)

    async def _ensure_event_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(fw_events)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "mtime" not in columns:
            await db.execute("ALTER TABLE fw_events ADD COLUMN mtime REAL")
        if "size" not in columns:
            await db.execute("ALTER TABLE fw_events ADD COLUMN size INTEGER")
        if "inode" not in columns:
            await db.execute("ALTER TABLE fw_events ADD COLUMN inode INTEGER")

    async def close(self) -> None:
        self._initialized = False

    # ── Watcher CRUD ────────────────────────────────────────────────

    async def upsert_watcher(
        self,
        watcher_id: str,
        path: str,
        config_hash: str,
        config: dict[str, Any],
    ) -> None:
        async with self._db_connection() as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                INSERT INTO fw_watchers (id, path, config_hash, config_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    path = excluded.path,
                    config_hash = excluded.config_hash,
                    config_json = excluded.config_json
                """,
                (watcher_id, path, config_hash, json.dumps(config), time.time()),
            )
            await db.commit()

    async def get_watcher(self, watcher_id: str) -> dict[str, Any] | None:
        async with self._db_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM fw_watchers WHERE id = ?", (watcher_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_watcher_by_hash(self, config_hash: str) -> dict[str, Any] | None:
        async with self._db_connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM fw_watchers
                WHERE config_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (config_hash,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_watchers(self) -> list[dict[str, Any]]:
        async with self._db_connection() as db:
            cursor = await db.execute("SELECT * FROM fw_watchers ORDER BY created_at")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_watcher(self, watcher_id: str) -> None:
        async with self._db_connection() as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("DELETE FROM fw_watchers WHERE id = ?", (watcher_id,))
            await db.commit()
        self.notifier.remove(watcher_id)

    def notify_watcher_closed(self, watcher_id: str) -> None:
        self.notifier.notify(watcher_id)

    # ── Events ──────────────────────────────────────────────────────

    async def get_latest_seq(self, watcher_id: str) -> int:
        async with self._db_connection() as db:
            cursor = await db.execute(
                "SELECT MAX(seq) FROM fw_events WHERE watcher_id = ?",
                (watcher_id,),
            )
            row = await cursor.fetchone()
            if not row or row[0] is None:
                return 0
            return int(row[0])

    async def insert_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        async with self._db_connection() as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executemany(
                """
                INSERT INTO fw_events (
                    watcher_id, type, path, relative_path, old_path,
                    is_dir, timestamp, mtime, size, inode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e["watcher_id"],
                        e["type"],
                        e["path"],
                        e.get("relative_path"),
                        e.get("old_path"),
                        int(e["is_dir"]),
                        e["timestamp"],
                        e.get("mtime"),
                        e.get("size"),
                        e.get("inode"),
                    )
                    for e in events
                ],
            )
            await db.commit()

        self._insert_count += len(events)

        # Notify per watcher_id
        watcher_ids = {e["watcher_id"] for e in events}
        for wid in watcher_ids:
            self.notifier._ensure_key(wid)
            self.notifier.notify(wid)

        # Auto-cleanup every 1000 inserts
        if self._insert_count >= 1000:
            self._insert_count = 0
            await self.cleanup_events(keep=self._buffer_size)

    async def get_events_since(
        self, watcher_id: str, last_seq: int
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Return (events, cursor, overflow)."""
        async with self._db_connection() as db:
            # Check overflow: if last_seq > 0 and < MIN(seq) for this watcher
            overflow = False
            if last_seq > 0:
                cursor = await db.execute(
                    "SELECT MIN(seq) FROM fw_events WHERE watcher_id = ?",
                    (watcher_id,),
                )
                row = await cursor.fetchone()
                min_seq = row[0] if row else None
                if min_seq is not None and last_seq < min_seq:
                    overflow = True

            cursor = await db.execute(
                """
                SELECT
                    seq, watcher_id, type, path, relative_path, old_path,
                    is_dir, timestamp, mtime, size, inode
                FROM fw_events
                WHERE watcher_id = ? AND seq > ?
                ORDER BY seq
                """,
                (watcher_id, last_seq),
            )
            rows = await cursor.fetchall()
            events = [dict(r) for r in rows]
            new_cursor = events[-1]["seq"] if events else last_seq
            return events, new_cursor, overflow

    async def wait_for_events(
        self,
        watcher_id: str,
        last_seq: int,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Wait until events newer than *last_seq* are available or timeout expires."""
        version = self.notifier.get_version(watcher_id)
        events, new_cursor, overflow = await self.get_events_since(watcher_id, last_seq)
        if events or overflow or timeout == 0:
            return events, new_cursor, overflow

        try:
            await asyncio.wait_for(
                self.notifier.wait(watcher_id, version),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return [], last_seq, False

        return await self.get_events_since(watcher_id, last_seq)

    async def cleanup_events(self, keep: int | None = None) -> None:
        """Delete old events, keeping only the most recent *keep* rows per watcher."""
        keep = keep or self._buffer_size
        async with self._db_connection() as db:
            await db.execute(
                """
                DELETE FROM fw_events
                WHERE seq IN (
                    SELECT seq
                    FROM (
                        SELECT
                            seq,
                            ROW_NUMBER() OVER (
                                PARTITION BY watcher_id
                                ORDER BY seq DESC
                            ) AS rn
                        FROM fw_events
                    )
                    WHERE rn > ?
                )
                """,
                (keep,),
            )
            await db.commit()
