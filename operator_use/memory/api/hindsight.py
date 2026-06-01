from __future__ import annotations

import asyncio
import os
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions, MemoryContext, MemorySearchResult


class HindsightMemoryAPI(BaseMemoryAPI):
    """Hindsight (Vectorize) memory provider using the optional HTTP client.

    Wraps ``hindsight_client.Hindsight`` and maps its three core operations onto
    the provider interface:

    - ``recall``  → ``search`` / fact-based ``prefetch``
    - ``retain``  → ``remember`` / turn sync
    - ``reflect`` → cross-memory synthesis (``reflect`` / synthesized ``prefetch``)

    Memories are scoped to a single Hindsight *bank* (``bank_id``). ``prefetch``
    can return either raw recalled facts (``prefetch_method="recall"``, the
    default) or an LLM-synthesized answer (``prefetch_method="reflect"``).
    """

    def __init__(self, options: MemoryOptions | None = None, client: Any | None = None) -> None:
        self.options = options or MemoryOptions(api_key_env="HINDSIGHT_API_KEY")
        self.context = MemoryContext()
        self._client = client
        cfg = self.options.config or {}
        self._bank_id = cfg.get("bank_id") or os.environ.get("HINDSIGHT_BANK_ID") or "operator"
        self._budget = cfg.get("budget") or os.environ.get("HINDSIGHT_RECALL_BUDGET") or "mid"
        self._prefetch_method = cfg.get("prefetch_method", "recall")
        self._recall_max_tokens = cfg.get("recall_max_tokens")

    def initialize(self, context: MemoryContext) -> None:
        super().initialize(context)
        cfg = self.options.config or {}
        self._bank_id = cfg.get("bank_id") or os.environ.get("HINDSIGHT_BANK_ID") or self.options.user_id or context.user_id or "operator"
        if self._client is None:
            self._client = self._create_client()

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch:
            return ""
        if self._prefetch_method == "reflect":
            text = await self.reflect(query, session_id=session_id)
            if not text.strip():
                return ""
            return "\n".join(["## Recalled Memory", "", "Hindsight synthesis for the current turn:", "", text])
        results = await asyncio.to_thread(self.search, query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", "", "Relevant Hindsight memories for the current turn:"]
        for result in results:
            lines.append(f"- {result.content}")
        return "\n".join(lines)

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        if self._client is None:
            return []

        raw = self._recall(query)
        items = self._extract_results(raw)
        results: list[MemorySearchResult] = []
        for item in items[:limit]:
            content = self._get_value(item, "text") or self._get_value(item, "content") or str(item)
            source = self._get_value(item, "type") or "hindsight"
            results.append(MemorySearchResult(
                source=str(source),
                content=str(content),
                score=0.0,
                metadata=item if isinstance(item, dict) else None,
            ))
        return results

    async def reflect(self, query: str, *, session_id: str = "") -> str:
        if self._client is None or not query.strip():
            return ""
        return await asyncio.to_thread(self._reflect, query)

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        if self._client is None or not content.strip():
            return ""
        await asyncio.to_thread(self._retain, content, metadata or {"source": "operator-tool"})
        return self._bank_id

    async def forget(self, memory_id: str) -> bool:
        if self._client is None:
            return False
        for method_name in ("delete", "forget", "delete_memory"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                await asyncio.to_thread(method, bank_id=self._bank_id, memory_id=memory_id)
                return True
            except TypeError:
                try:
                    await asyncio.to_thread(method, memory_id)
                    return True
                except TypeError:
                    continue
        return False

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self.options.sync_turns or self._client is None:
            return
        content = f"User: {user_content}\nAssistant: {assistant_content}"
        metadata = {"source": "operator", "session_id": session_id} if session_id else {"source": "operator"}
        await asyncio.to_thread(self._retain, content, metadata)

    def _create_client(self) -> Any:
        try:
            from hindsight_client import Hindsight  # type: ignore
        except Exception as exc:
            raise ImportError("hindsight provider requires the optional 'hindsight-client' package.") from exc

        cfg = self.options.config or {}
        api_key = os.environ.get(self.options.api_key_env or "HINDSIGHT_API_KEY")
        base_url = cfg.get("api_url") or os.environ.get("HINDSIGHT_API_URL") or "https://api.hindsight.vectorize.io"
        kwargs: dict[str, Any] = {"base_url": base_url}
        if api_key:
            kwargs["api_key"] = api_key
        return Hindsight(**kwargs)

    def _recall(self, query: str) -> Any:
        client = self._client
        if client is None:
            return None
        kwargs: dict[str, Any] = {"bank_id": self._bank_id, "query": query, "budget": self._budget}
        if self._recall_max_tokens:
            kwargs["max_tokens"] = self._recall_max_tokens
        try:
            return client.recall(**kwargs)
        except TypeError:
            return client.recall(bank_id=self._bank_id, query=query)

    def _reflect(self, query: str) -> str:
        client = self._client
        if client is None:
            return ""
        try:
            resp = client.reflect(bank_id=self._bank_id, query=query, budget=self._budget)
        except TypeError:
            resp = client.reflect(bank_id=self._bank_id, query=query)
        return str(self._get_value(resp, "text") or "")

    def _retain(self, content: str, metadata: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.retain(bank_id=self._bank_id, content=content, metadata=metadata)
        except TypeError:
            client.retain(bank_id=self._bank_id, content=content)

    def _extract_results(self, raw: Any) -> list[Any]:
        if raw is None:
            return []
        if isinstance(raw, dict):
            value = raw.get("results", [])
            return value if isinstance(value, list) else []
        value = getattr(raw, "results", None)
        if isinstance(value, list):
            return value
        if isinstance(raw, list):
            return raw
        return []

    def _get_value(self, item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)
