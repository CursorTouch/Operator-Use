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


_DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ~130MB, CPU-only


class LocalMemoryAPI(BaseMemoryAPI):
    """File-backed memory provider. No external services or API keys required.

    Stores facts as JSONL under root_dir/memories.jsonl. When ``fastembed`` is
    available, retrieval ranks by embedding cosine similarity (local, no network)
    blended with a recency bias; vectors are cached in a sidecar ``vectors.json``
    so the JSONL stays human-readable and nothing is re-embedded on restart. If
    fastembed is missing or fails to load, retrieval transparently falls back to
    keyword-overlap scoring.
    """

    def __init__(self, options: MemoryOptions | None = None) -> None:
        super().__init__(options or MemoryOptions())
        self._store_path: Path | None = None
        self._vectors_path: Path | None = None
        self._vectors: dict[str, list[float]] = {}
        self._model: Any = None
        self._model_ready = False  # True once we've tried to load (success or failure)

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
        self._vectors_path = self._store_path.parent / "vectors.json"
        if self._vectors_path.exists():
            try:
                self._vectors = json.loads(self._vectors_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._vectors = {}

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
        if self._ensure_model() is None:
            return self._keyword_search(query, entries, limit)
        return self._semantic_search(query, entries, limit)

    def _keyword_search(
        self, query: str, entries: list[dict[str, Any]], limit: int
    ) -> list[MemorySearchResult]:
        query_words = _tokenize(query)
        now_ts = datetime.now(timezone.utc).timestamp()
        scored: list[tuple[float, dict]] = []
        for entry in entries:
            overlap = _overlap_score(query_words, _tokenize(entry.get("content", "")))
            if overlap == 0.0:
                continue
            age_days = (now_ts - entry.get("created_ts", now_ts)) / 86400
            recency = 1.0 / (1.0 + age_days / 30)  # half-weight at 30 days
            scored.append((overlap * 0.7 + recency * 0.3, entry))
        return self._rank(scored, limit)

    def _semantic_search(
        self, query: str, entries: list[dict[str, Any]], limit: int
    ) -> list[MemorySearchResult]:
        import numpy as np

        self._backfill_vectors(entries)
        q_vec = np.asarray(self._embed(query))
        min_relevance = float((self.options.config or {}).get("min_relevance", 0.5))
        now_ts = datetime.now(timezone.utc).timestamp()
        scored: list[tuple[float, dict]] = []
        for entry in entries:
            vec = self._vectors.get(entry.get("id", ""))
            if not vec:
                continue
            sim = _cosine(q_vec, np.asarray(vec))
            if sim < min_relevance:
                continue
            age_days = (now_ts - entry.get("created_ts", now_ts)) / 86400
            recency = 1.0 / (1.0 + age_days / 30)  # half-weight at 30 days
            scored.append((sim * 0.7 + recency * 0.3, entry))
        return self._rank(scored, limit)

    @staticmethod
    def _rank(scored: list[tuple[float, dict]], limit: int) -> list[MemorySearchResult]:
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
        live = {e.get("id") for e in entries}
        if len(live) != len(self._vectors):
            self._vectors = {k: v for k, v in self._vectors.items() if k in live}
            self._persist_vectors()

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
        if self._ensure_model() is not None:
            self._vectors[entry_id] = list(self._embed(content))
            self._persist_vectors()
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

    # ── embedding helpers ─────────────────────────────────────────────────────

    def _ensure_model(self) -> Any:
        """Lazily load the fastembed model. Returns None if unavailable."""
        if self._model_ready:
            return self._model
        self._model_ready = True
        try:
            from fastembed import TextEmbedding

            model_name = (self.options.config or {}).get("embed_model", _DEFAULT_EMBED_MODEL)
            self._model = TextEmbedding(model_name=model_name)
        except Exception:
            self._model = None
        return self._model

    def _embed(self, text: str) -> list[float]:
        return next(iter(self._model.embed([text]))).tolist()

    def _backfill_vectors(self, entries: list[dict[str, Any]]) -> None:
        """Embed entries that have no cached vector (e.g. written before fastembed)."""
        missing = [e for e in entries if e.get("id") and e["id"] not in self._vectors]
        if not missing:
            return
        for vec, entry in zip(self._model.embed([e["content"] for e in missing]), missing):
            self._vectors[entry["id"]] = vec.tolist()
        self._persist_vectors()

    def _persist_vectors(self) -> None:
        if self._vectors_path is not None:
            self._vectors_path.write_text(json.dumps(self._vectors), encoding="utf-8")


# ── module-level utilities ────────────────────────────────────────────────────


def _cosine(a: Any, b: Any) -> float:
    import numpy as np

    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0


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
