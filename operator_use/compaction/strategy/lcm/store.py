"""SQLite message store — persists raw archived messages for LCM recall."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from operator_use.compaction.strategy.lcm.types import StoredMessage


def _text_from_content(content: Any) -> str:
    """Extract plain text from message content (str or multimodal list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(content or "")


class MessageStore:
    """Append-only SQLite store for archived messages with FTS5 search."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._init()

    def _init(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                store_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content_text TEXT NOT NULL DEFAULT '',
                content_json TEXT NOT NULL DEFAULT '{}',
                timestamp   REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON messages(session_id, timestamp);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content_text, content=messages, content_rowid=store_id,
                           tokenize='porter ascii');

            CREATE TRIGGER IF NOT EXISTS msg_fts_insert
                AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content_text)
                    VALUES (new.store_id, new.content_text);
                END;
        """)
        self._conn.commit()

    # -------------------------------------------------------------------------
    # Write
    # -------------------------------------------------------------------------

    def persist(self, session_id: str, messages: list[Any]) -> list[int]:
        """Persist a list of AgentMessage-like objects, return their store_ids."""
        store_ids: list[int] = []
        now = time.time()
        for msg in messages:
            role = getattr(msg, "role", "unknown")
            if hasattr(role, "value"):
                role = role.value
            raw_content = getattr(msg, "contents", None) or getattr(msg, "content", "")
            content_json = json.dumps(
                raw_content if isinstance(raw_content, (str, list, dict))
                else [c.model_dump() if hasattr(c, "model_dump") else str(c) for c in (raw_content or [])]
            )
            content_text = _text_from_content(raw_content)
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content_text, content_json, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, str(role), content_text, content_json, now),
            )
            store_ids.append(cur.lastrowid)
        self._conn.commit()
        return store_ids

    # -------------------------------------------------------------------------
    # Read
    # -------------------------------------------------------------------------

    def get_by_ids(self, store_ids: list[int]) -> list[StoredMessage]:
        if not store_ids:
            return []
        placeholders = ",".join("?" * len(store_ids))
        rows = self._conn.execute(
            f"SELECT store_id, session_id, role, content_text, content_json, timestamp "
            f"FROM messages WHERE store_id IN ({placeholders}) ORDER BY timestamp",
            store_ids,
        ).fetchall()
        return [StoredMessage(*r) for r in rows]

    def search(self, query: str, session_id: str, limit: int = 10) -> list[StoredMessage]:
        """FTS5 search across archived messages for a session."""
        try:
            rows = self._conn.execute(
                """SELECT m.store_id, m.session_id, m.role, m.content_text, m.content_json, m.timestamp
                   FROM messages_fts fts
                   JOIN messages m ON m.store_id = fts.rowid
                   WHERE messages_fts MATCH ? AND m.session_id = ?
                   ORDER BY rank LIMIT ?""",
                (query, session_id, limit),
            ).fetchall()
        except sqlite3.Error:
            # fallback to LIKE
            rows = self._conn.execute(
                "SELECT store_id, session_id, role, content_text, content_json, timestamp "
                "FROM messages WHERE session_id = ? AND content_text LIKE ? LIMIT ?",
                (session_id, f"%{query}%", limit),
            ).fetchall()
        return [StoredMessage(*r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
