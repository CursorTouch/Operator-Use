"""SQLite-backed summary DAG for LCM hierarchical context retrieval."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from operator_use.compaction.strategy.lcm.types import LCMNode


class SummaryDAG:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._init()

    def _init(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                depth       INTEGER NOT NULL DEFAULT 0,
                summary     TEXT NOT NULL,
                source_ids  TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL DEFAULT 'messages',
                expand_hint TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_session_depth
                ON nodes(session_id, depth, created_at);

            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts
                USING fts5(summary, content=nodes, content_rowid=node_id,
                           tokenize='porter ascii');

            CREATE TRIGGER IF NOT EXISTS nodes_fts_insert
                AFTER INSERT ON nodes BEGIN
                    INSERT INTO nodes_fts(rowid, summary)
                    VALUES (new.node_id, new.summary);
                END;
        """)
        self._conn.commit()

    # -------------------------------------------------------------------------
    # Write
    # -------------------------------------------------------------------------

    def add_node(self, node: LCMNode) -> int:
        cur = self._conn.execute(
            "INSERT INTO nodes(session_id, depth, summary, source_ids, source_type, expand_hint, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                node.session_id,
                node.depth,
                node.summary,
                json.dumps(node.source_ids),
                node.source_type,
                node.expand_hint,
                node.created_at or time.time(),
            ),
        )
        self._conn.commit()
        node.node_id = cur.lastrowid
        return cur.lastrowid

    # -------------------------------------------------------------------------
    # Read
    # -------------------------------------------------------------------------

    def get_node(self, node_id: int) -> LCMNode | None:
        row = self._conn.execute(
            "SELECT node_id, session_id, depth, summary, source_ids, source_type, expand_hint, created_at "
            "FROM nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_uncondensed(self, session_id: str, depth: int) -> list[LCMNode]:
        """Nodes at `depth` not yet referenced as sources by any higher-depth node."""
        rows = self._conn.execute(
            """SELECT n.node_id, n.session_id, n.depth, n.summary,
                      n.source_ids, n.source_type, n.expand_hint, n.created_at
               FROM nodes n
               WHERE n.session_id = ? AND n.depth = ?
               AND n.node_id NOT IN (
                   SELECT CAST(j.value AS INTEGER)
                   FROM nodes p, json_each(p.source_ids) j
                   WHERE p.session_id = ? AND p.depth > ? AND p.source_type = 'nodes'
               )
               ORDER BY n.created_at""",
            (session_id, depth, session_id, depth),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_top_level(self, session_id: str) -> LCMNode | None:
        """The single deepest / most recent node — the active summary anchor."""
        row = self._conn.execute(
            """SELECT node_id, session_id, depth, summary, source_ids,
                      source_type, expand_hint, created_at
               FROM nodes
               WHERE session_id = ?
               ORDER BY depth DESC, created_at DESC
               LIMIT 1""",
            (session_id,),
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_children(self, node: LCMNode) -> list[LCMNode]:
        """Child nodes (source_type='nodes') of a higher-depth node."""
        if node.source_type != "nodes" or not node.source_ids:
            return []
        placeholders = ",".join("?" * len(node.source_ids))
        rows = self._conn.execute(
            f"SELECT node_id, session_id, depth, summary, source_ids, source_type, expand_hint, created_at "
            f"FROM nodes WHERE node_id IN ({placeholders}) ORDER BY created_at",
            node.source_ids,
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def search(self, query: str, session_id: str, limit: int = 10) -> list[LCMNode]:
        try:
            rows = self._conn.execute(
                """SELECT n.node_id, n.session_id, n.depth, n.summary,
                          n.source_ids, n.source_type, n.expand_hint, n.created_at
                   FROM nodes_fts fts
                   JOIN nodes n ON n.node_id = fts.rowid
                   WHERE nodes_fts MATCH ? AND n.session_id = ?
                   ORDER BY rank LIMIT ?""",
                (query, session_id, limit),
            ).fetchall()
        except sqlite3.Error:
            rows = self._conn.execute(
                "SELECT node_id, session_id, depth, summary, source_ids, source_type, expand_hint, created_at "
                "FROM nodes WHERE session_id = ? AND summary LIKE ? LIMIT ?",
                (session_id, f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_node(r) for r in rows]

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _row_to_node(self, row: tuple) -> LCMNode:
        node_id, session_id, depth, summary, source_ids, source_type, expand_hint, created_at = row
        n = LCMNode(
            session_id=session_id,
            depth=depth,
            summary=summary,
            source_ids=json.loads(source_ids) if source_ids else [],
            source_type=source_type,
            expand_hint=expand_hint or "",
            created_at=created_at or 0.0,
        )
        n.node_id = node_id
        return n

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
