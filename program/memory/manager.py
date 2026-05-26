from __future__ import annotations

from program.memory.api.base import BaseMemoryAPI
from program.memory.api.registry import MemoryAPIRegistry
from program.memory.provider.registry import MemoryProviderRegistry
from program.memory.types import MemoryRuntimeContext


class MemoryManager:
    """Owns the optional active external memory provider.

    This keeps one external provider active at a time and avoids prompt/tool
    bloat from multiple competing backends.
    """

    def __init__(
        self,
        provider_id: str | None = None,
        providers: MemoryProviderRegistry | None = None,
        apis: MemoryAPIRegistry | None = None,
    ) -> None:
        self.providers = providers or MemoryProviderRegistry.from_builtins()
        self.apis = apis or MemoryAPIRegistry.from_builtins()
        self.provider_id = provider_id
        self.api: BaseMemoryAPI | None = None

    def initialize(self, context: MemoryRuntimeContext) -> BaseMemoryAPI | None:
        if not self.provider_id:
            return None

        provider = self.providers.get(self.provider_id)
        if provider is None:
            raise ValueError(f"Memory provider '{self.provider_id}' not found.")

        api_class = provider.api
        if isinstance(api_class, str):
            resolved = self.apis.get(api_class)
            if resolved is None:
                raise ValueError(f"Memory API '{api_class}' not found.")
            api_class = resolved

        api = api_class(provider.options)
        api.initialize(context)
        self.api = api
        return api

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self.api is None:
            return ""
        return await self.api.prefetch(query, session_id=session_id)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self.api is not None:
            self.api.queue_prefetch(query, session_id=session_id)

    def search(self, query: str, *, limit: int = 5):
        if self.api is None:
            return []
        return self.api.search(query, limit=limit)

    async def remember(self, content: str, metadata: dict | None = None) -> str:
        if self.api is not None:
            return await self.api.remember(content, metadata)
        return ""

    async def forget(self, memory_id: str) -> bool:
        if self.api is None:
            return False
        return await self.api.forget(memory_id)

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self.api is not None:
            await self.api.on_turn_complete(user_content, assistant_content, session_id=session_id)

    async def on_session_end(self, messages: list[dict]) -> None:
        if self.api is not None:
            await self.api.on_session_end(messages)

    async def on_pre_compact(self, messages: list[dict]) -> str:
        if self.api is None:
            return ""
        return await self.api.on_pre_compact(messages)

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        if self.api is not None:
            await self.api.on_memory_write(action, target, content, metadata)

    async def shutdown(self) -> None:
        if self.api is not None:
            await self.api.shutdown()
