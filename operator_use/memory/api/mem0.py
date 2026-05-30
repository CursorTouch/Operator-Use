from __future__ import annotations

import asyncio
import os
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions, MemoryContext, MemorySearchResult


class Mem0MemoryAPI(BaseMemoryAPI):
    """Mem0 memory provider.

    Supports the documented OSS SDK shape:
    ``from mem0 import Memory`` with ``add(messages, user_id=...)`` and
    ``search(query, filters={"user_id": ...})``. If a Mem0 cloud client is
    installed and exported as ``MemoryClient``, this adapter can use it too.
    """

    def __init__(self, options: MemoryOptions | None = None, client: Any | None = None) -> None:
        self.options = options or MemoryOptions(api_key_env="MEM0_API_KEY")
        self.context = MemoryContext()
        self._client = client
        self._user_id = self.options.user_id or "operator-user"

    def initialize(self, context: MemoryContext) -> None:
        super().initialize(context)
        self._user_id = self.options.user_id or context.user_id or "operator-user"
        if self._client is None:
            self._client = self._create_client()

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch:
            return ""
        results = await asyncio.to_thread(self.search, query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", "", "Relevant Mem0 memories for the current turn:"]
        for result in results:
            lines.append(f"- {result.content}")
        return "\n".join(lines)

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        if self._client is None:
            return []

        raw = self._search_client(query, limit)
        items = self._extract_results(raw)
        results: list[MemorySearchResult] = []
        for item in items[:limit]:
            content = self._get_value(item, "memory") or self._get_value(item, "content") or str(item)
            score = self._get_value(item, "score") or 0.0
            source = self._get_value(item, "id") or "mem0"
            results.append(MemorySearchResult(
                source=str(source),
                content=str(content),
                score=float(score) if isinstance(score, int | float) else 0.0,
                metadata=item if isinstance(item, dict) else None,
            ))
        return results

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        if self._client is None or not content.strip():
            return ""
        await asyncio.to_thread(self._add_messages, [{"role": "user", "content": content}], "")
        return "mem0"

    async def forget(self, memory_id: str) -> bool:
        if self._client is None:
            return False
        for method_name in ("delete", "delete_memory"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                await asyncio.to_thread(method, memory_id)
                return True
            except TypeError:
                await asyncio.to_thread(method, memory_id=memory_id)
                return True
        return False

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self.options.sync_turns or self._client is None:
            return
        messages = [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
        await asyncio.to_thread(self._add_messages, messages, session_id)

    def _create_client(self) -> Any:
        try:
            from mem0 import MemoryClient  # type: ignore
            api_key = os.environ.get(self.options.api_key_env or "MEM0_API_KEY")
            return MemoryClient(api_key=api_key) if api_key else MemoryClient()
        except Exception:
            try:
                from mem0 import Memory  # type: ignore
                return Memory()
            except Exception as exc:
                raise ImportError("mem0 provider requires the optional 'mem0ai' package.") from exc

    def _search_client(self, query: str, limit: int) -> Any:
        client = self._client
        if client is None:
            return []
        filters = {"user_id": self._user_id}
        try:
            return client.search(query, filters=filters, top_k=limit)
        except TypeError:
            try:
                return client.search(query, user_id=self._user_id, limit=limit)
            except TypeError:
                return client.search(query)

    def _add_messages(self, messages: list[dict[str, str]], session_id: str) -> None:
        client = self._client
        if client is None:
            return
        kwargs: dict[str, Any] = {"user_id": self._user_id}
        if self.options.agent_id:
            kwargs["agent_id"] = self.options.agent_id
        if session_id:
            kwargs["run_id"] = session_id
        try:
            client.add(messages, **kwargs)
        except TypeError:
            client.add(messages, user_id=self._user_id)

    def _extract_results(self, raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            value = raw.get("results", raw.get("memories", []))
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
