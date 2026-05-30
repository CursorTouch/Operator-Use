from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryContext, MemoryOptions, MemorySearchResult

_EXTRACT_SYSTEM = """\
You are a memory extraction assistant. Given a conversation turn, extract every \
durable, reusable fact worth remembering long-term. Ignore pleasantries, filler, \
and in-session-only details. Output one fact per line, plain text, no bullets or \
numbering. If there is nothing worth remembering, output the single word NONE."""


class LocalMemoryAPI(BaseMemoryAPI):
    """File-backed memory provider. No external services or API keys required.

    Stores facts as JSONL under root_dir/memories.jsonl. Retrieval uses
    keyword-overlap scoring with a recency bias — no embeddings needed.
    """

    def __init__(self, options: MemoryOptions | None = None) -> None:
        super().__init__(options or MemoryOptions())
        self._store_path: Path | None = None

    def initialize(self, context: MemoryContext) -> None:
        super().initialize(context)
        root = self.options.root_dir or context.project_memory_dir
        if root is None:
            raise ValueError(
                "LocalMemoryAPI requires a profile to be active. "
                "No memory directory is available."
            )
        self._store_path = Path(root) / "memories.jsonl"
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────────────────────

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch or not query.strip():
            return ""
        results = self.search(query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", ""]
        for r in results:
            lines.append(f"- {r.content}")
        return "\n".join(lines)

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        entries = self._load()
        if not entries:
            return []
        query_words = _tokenize(query)
        scored: list[tuple[float, dict]] = []
        now_ts = datetime.now(timezone.utc).timestamp()
        for entry in entries:
            content = entry.get("content", "")
            overlap = _overlap_score(query_words, _tokenize(content))
            if overlap == 0.0:
                continue
            age_days = (now_ts - entry.get("created_ts", now_ts)) / 86400
            recency = 1.0 / (1.0 + age_days / 30)  # half-weight at 30 days
            scored.append((overlap * 0.7 + recency * 0.3, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemorySearchResult(
                source=e.get("id", "local"),
                content=e["content"],
                score=round(score, 4),
            )
            for score, e in scored[:limit]
        ]

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        if not content.strip():
            return ""
        entry_id = self._append(content, source="manual", metadata=metadata)
        return entry_id

    async def forget(self, memory_id: str) -> bool:
        entries = self._load()
        before = len(entries)
        entries = [e for e in entries if e.get("id") != memory_id]
        if len(entries) == before:
            return False
        self._save(entries)
        return True

    async def on_turn_complete(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        if not self.options.sync_turns:
            return
        facts = await self._extract_facts(user_content, assistant_content)
        for fact in facts:
            self._append(fact, source="turn", session_id=session_id)

    async def on_pre_compact(self, messages: list[dict[str, Any]]) -> str:
        # Extract facts from the full message list before it's compacted away.
        pairs = _adjacent_pairs(messages)
        for user_text, asst_text in pairs:
            facts = await self._extract_facts(user_text, asst_text)
            for fact in facts:
                self._append(fact, source="compact")
        return ""

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "local_memory_search",
                "description": "Search local long-term memory for relevant facts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "local_memory_store",
                "description": "Store a durable fact in local long-term memory.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        ]

    async def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if name == "local_memory_search":
            results = self.search(str(args.get("query", "")), limit=int(args.get("limit", 5)))
            return json.dumps([r.__dict__ for r in results])
        if name == "local_memory_store":
            entry_id = await self.remember(str(args.get("content", "")))
            return json.dumps({"ok": bool(entry_id), "id": entry_id})
        return await super().handle_tool_call(name, args, **kwargs)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _load(self) -> list[dict[str, Any]]:
        if self._store_path is None or not self._store_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._store_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries

    def _save(self, entries: list[dict[str, Any]]) -> None:
        if self._store_path is None:
            return
        self._store_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

    def _append(
        self,
        content: str,
        *,
        source: str = "manual",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if self._store_path is None:
            return ""
        entry_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "id": entry_id,
            "content": content,
            "source": source,
            "created_at": now.isoformat(),
            "created_ts": now.timestamp(),
        }
        if session_id:
            entry["session_id"] = session_id
        if metadata:
            entry["metadata"] = metadata
        with self._store_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry_id

    async def _extract_facts(self, user_content: str, assistant_content: str) -> list[str]:
        llm = self.context.llm
        if llm is None:
            return []
        try:
            from operator_use.inference.types import LLMContext
            from operator_use.message.types import UserMessage

            turn_text = f"User: {user_content}\nAssistant: {assistant_content}"
            ctx = LLMContext(
                messages=[UserMessage.text(turn_text)],
                system_prompt=_EXTRACT_SYSTEM,
            )
            events = await llm.invoke(ctx)
            text = _collect_text(events).strip()
            if not text or text.upper() == "NONE":
                return []
            return [line.strip() for line in text.splitlines() if line.strip()]
        except Exception:
            return []


# ── module-level utilities ────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    import re
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _overlap_score(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _collect_text(events: list[Any]) -> str:
    parts: list[str] = []
    for event in events:
        data = getattr(event, "data", None)
        text_obj = getattr(data, "text", None)
        if text_obj is not None:
            parts.append(getattr(text_obj, "content", ""))
    return "".join(parts)


def _adjacent_pairs(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Yield (user_text, assistant_text) pairs from a raw message list."""
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(messages) - 1:
        m = messages[i]
        n = messages[i + 1]
        if m.get("role") == "user" and n.get("role") == "assistant":
            pairs.append((_message_text(m), _message_text(n)))
            i += 2
        else:
            i += 1
    return pairs


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    return str(content)
