from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions, MemoryRuntimeContext, MemorySearchResult


class SupermemoryAPI(BaseMemoryAPI):
    """Supermemory provider using the optional Python SDK."""

    def __init__(self, options: MemoryOptions | None = None, client: Any | None = None) -> None:
        self.options = options or MemoryOptions(api_key_env="SUPERMEMORY_API_KEY")
        self.context = MemoryRuntimeContext()
        self._client = client
        self._container_tag = self.options.user_id or "operator-user"

    def initialize(self, context: MemoryRuntimeContext) -> None:
        super().initialize(context)
        self._container_tag = self.options.user_id or context.user_id or "operator-user"
        if self._client is None:
            self._client = self._create_client()

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch:
            return ""
        results = await asyncio.to_thread(self.search, query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", "", "Relevant Supermemory memories for the current turn:"]
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
            content = (
                self._get_value(item, "memory")
                or self._get_value(item, "chunk")
                or self._get_value(item, "content")
                or self._chunk_content(item)
                or str(item)
            )
            score = self._get_value(item, "similarity") or self._get_value(item, "score") or 0.0
            source = self._get_value(item, "id") or self._get_value(item, "document_id") or "supermemory"
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
        await asyncio.to_thread(self._add_content, content, metadata or {"source": "operator-tool"})
        return "supermemory"

    async def forget(self, memory_id: str) -> bool:
        if self._client is None:
            return False
        for owner in (self._client, getattr(self._client, "memories", None), getattr(self._client, "documents", None)):
            if owner is None:
                continue
            method = getattr(owner, "delete", None)
            if method is None:
                continue
            try:
                await asyncio.to_thread(method, memory_id)
                return True
            except TypeError:
                await asyncio.to_thread(method, id=memory_id)
                return True
        return False

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self.options.sync_turns or self._client is None:
            return
        content = f"User: {user_content}\nAssistant: {assistant_content}"
        metadata = {"source": "operator", "session_id": session_id} if session_id else {"source": "operator"}
        await asyncio.to_thread(self._add_content, content, metadata)

    async def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if name == "supermemory_search":
            query = str(args.get("query", ""))
            limit = int(args.get("limit", 5))
            return json.dumps([r.__dict__ for r in self.search(query, limit=limit)])
        if name == "supermemory_store":
            content = str(args.get("content", ""))
            source = await self.remember(content, {"source": "operator-tool"})
            return json.dumps({"ok": bool(source), "source": source})
        return await super().handle_tool_call(name, args, **kwargs)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "supermemory_search",
                "description": "Search Supermemory long-term memory.",
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
                "name": "supermemory_store",
                "description": "Store a durable fact in Supermemory.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        ]

    def _create_client(self) -> Any:
        try:
            from supermemory import Supermemory  # type: ignore
        except Exception as exc:
            raise ImportError("supermemory provider requires the optional 'supermemory' package.") from exc

        api_key = os.environ.get(self.options.api_key_env or "SUPERMEMORY_API_KEY")
        return Supermemory(api_key=api_key) if api_key else Supermemory()

    def _search_client(self, query: str, limit: int) -> Any:
        search = getattr(self._client, "search")
        memories = getattr(search, "memories", None)
        if memories is not None:
            try:
                return memories(q=query, container_tag=self._container_tag, search_mode="hybrid", limit=limit)
            except TypeError:
                return memories(q=query, containerTag=self._container_tag, searchMode="hybrid", limit=limit)
        execute = getattr(search, "execute")
        try:
            return execute(q=query, container_tag=self._container_tag, limit=limit)
        except TypeError:
            return execute(q=query, limit=limit)

    def _add_content(self, content: str, metadata: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.add(content=content, container_tag=self._container_tag, metadata=metadata)
        except TypeError:
            client.add(content=content, containerTag=self._container_tag, metadata=metadata)

    def _extract_results(self, raw: Any) -> list[Any]:
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

    def _chunk_content(self, item: Any) -> str | None:
        chunks = self._get_value(item, "chunks")
        if isinstance(chunks, list) and chunks:
            first = chunks[0]
            value = self._get_value(first, "content")
            return str(value) if value is not None else None
        return None
