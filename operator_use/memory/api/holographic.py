from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryContext, MemoryOptions, MemorySearchResult


class HolographicMemoryAPI(BaseMemoryAPI):
    """Local fact store with trust scoring. No external services or API keys.

    Backed by SQLite + FTS5 full-text search. Each fact carries a trust score in
    [0, 1] that ranking blends with full-text relevance and recency. Trust is
    self-reinforced: facts surfaced by recall gain a small trust bump, so facts
    that prove useful across sessions rise over time.

    """

    def __init__(self, options: MemoryOptions | None = None) -> None:
        super().__init__(options or MemoryOptions())
        cfg = self.options.config or {}
        self._default_trust = float(cfg.get("default_trust", 0.5))
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def initialize(self, context: MemoryContext) -> None:
        super().initialize(context)
        cfg = self.options.config or {}
        db_path = cfg.get("db_path")
        if db_path:
            self._db_path = Path(db_path)
        else:
            root = self.options.root_dir or context.project_memory_dir
            if root is None:
                raise ValueError(
                    "HolographicMemoryAPI requires a profile to be active or an "
                    "explicit 'db_path'. No memory directory is available."
                )
            self._db_path = Path(root) / "holographic.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT,
                session_id TEXT,
                created_ts REAL,
                trust REAL DEFAULT 0.5,
                hits INTEGER DEFAULT 0
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(id UNINDEXED, content);
            """
        )
        self._conn.commit()

    # ── public API ────────────────────────────────────────────────────────────

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch or not query.strip():
            return ""
        results = await asyncio.to_thread(self.search, query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", "", "Relevant facts (with trust scores) for the current turn:"]
        for r in results:
            trust = (r.metadata or {}).get("trust")
            suffix = f"  _(trust {trust:.2f})_" if isinstance(trust, float) else ""
            lines.append(f"- {r.content}{suffix}")
        return "\n".join(lines)

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        if self._conn is None:
            return []
        with self._lock:
            rows = self._match(query, limit * 4)
            if not rows:
                return []
            now_ts = datetime.now(timezone.utc).timestamp()
            scored: list[tuple[float, sqlite3.Row]] = []
            top = max(len(rows), 1)
            for rank, row in enumerate(rows):
                relevance = 1.0 - (rank / top)  # FTS order → descending relevance
                trust = float(row["trust"] or self._default_trust)
                age_days = (now_ts - float(row["created_ts"] or now_ts)) / 86400
                recency = 1.0 / (1.0 + age_days / 30)
                scored.append((relevance * 0.5 + trust * 0.3 + recency * 0.2, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_rows = scored[:limit]
            self._reinforce([row["id"] for _, row in top_rows])
            return [
                MemorySearchResult(
                    source=row["id"],
                    content=row["content"],
                    score=round(score, 4),
                    metadata={"trust": float(row["trust"] or self._default_trust), "hits": int(row["hits"] or 0)},
                )
                for score, row in top_rows
            ]

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        if not content.strip():
            return ""
        return await asyncio.to_thread(self._insert, content, "manual", "")

    async def forget(self, memory_id: str) -> bool:
        if self._conn is None:
            return False
        with self._lock:
            cur = self._conn.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
            self._conn.execute("DELETE FROM facts_fts WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cur.rowcount > 0

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self.options.sync_turns or self._conn is None:
            return
        content = f"User: {user_content}\nAssistant: {assistant_content}"
        await asyncio.to_thread(self._insert, content, "turn", session_id)

    async def shutdown(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
            self._conn = None

    # ── internal helpers ──────────────────────────────────────────────────────

    def _match(self, query: str, limit: int) -> list[sqlite3.Row]:
        assert self._conn is not None
        tokens = re.findall(r"[A-Za-z0-9]+", query)
        if tokens:
            match_expr = " OR ".join(tokens)
            try:
                fts_ids = [
                    r["id"]
                    for r in self._conn.execute(
                        "SELECT id FROM facts_fts WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                        (match_expr, limit),
                    )
                ]
                if fts_ids:
                    placeholders = ",".join("?" * len(fts_ids))
                    rows = self._conn.execute(
                        f"SELECT * FROM facts WHERE id IN ({placeholders})", fts_ids
                    ).fetchall()
                    order = {fid: i for i, fid in enumerate(fts_ids)}
                    return sorted(rows, key=lambda row: order.get(row["id"], len(order)))
            except sqlite3.OperationalError:
                pass
        # Fallback: substring match over the most recent facts.
        like = f"%{query.strip()}%"
        return self._conn.execute(
            "SELECT * FROM facts WHERE content LIKE ? ORDER BY created_ts DESC LIMIT ?",
            (like, limit),
        ).fetchall()

    def _insert(self, content: str, source: str, session_id: str) -> str:
        if self._conn is None:
            return ""
        entry_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts (id, content, source, session_id, created_ts, trust, hits) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (entry_id, content, source, session_id or None, now, self._default_trust),
            )
            self._conn.execute(
                "INSERT INTO facts_fts (id, content) VALUES (?, ?)", (entry_id, content)
            )
            self._conn.commit()
        return entry_id

    def _reinforce(self, ids: list[str]) -> None:
        """Recalled facts gain a small trust bump (self-evolving trust)."""
        if not ids or self._conn is None:
            return
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE facts SET hits = hits + 1, trust = MIN(1.0, trust + 0.02) "
            f"WHERE id IN ({placeholders})",
            ids,
        )
        self._conn.commit()
