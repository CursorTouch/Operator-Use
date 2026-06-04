from __future__ import annotations

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.api.registry import MemoryAPIRegistry
from operator_use.memory.provider.registry import MemoryProviderRegistry
from operator_use.memory.types import MemoryContext


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

    def initialize(self, context: MemoryContext) -> BaseMemoryAPI | None:
        """Initialize the configured memory provider API.

        Args:
            context: MemoryContext with session and resource information.

        Returns:
            The initialized API instance, or None if no provider is configured.

        Raises:
            ValueError: If the configured provider or API class is not found.
        """
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
        """Retrieve context-relevant memory for a query before the turn starts.

        Args:
            query: The query string to search memory.
            session_id: Optional session ID for scoped recall.

        Returns:
            Context text to inject into the system prompt, or empty string if no API.
        """
        if self.api is None:
            return ""
        return await self.api.prefetch(query, session_id=session_id)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a memory prefetch to run asynchronously (fire-and-forget).

        Args:
            query: The query string to search memory.
            session_id: Optional session ID for scoped recall.
        """
        if self.api is not None:
            self.api.queue_prefetch(query, session_id=session_id)

    def search(self, query: str, *, limit: int = 5):
        """Search memory synchronously with a limit on results.

        Args:
            query: The search query string.
            limit: Maximum number of results to return (default 5).

        Returns:
            List of matching memory items, or empty list if no API.
        """
        if self.api is None:
            return []
        return self.api.search(query, limit=limit)

    async def reflect(self, query: str, *, session_id: str = "") -> str:
        """Generate a reflection or summary based on memory context.

        Args:
            query: The reflection query or topic.
            session_id: Optional session ID for scoped reflection.

        Returns:
            Reflection text, or empty string if no API.
        """
        if self.api is None:
            return ""
        return await self.api.reflect(query, session_id=session_id)

    async def remember(self, content: str, metadata: dict | None = None) -> str:
        """Store new content in memory.

        Args:
            content: The content to remember.
            metadata: Optional metadata dict for the memory item.

        Returns:
            Memory ID of the stored item, or empty string if no API.
        """
        if self.api is not None:
            return await self.api.remember(content, metadata)
        return ""

    async def forget(self, memory_id: str) -> bool:
        """Delete a memory item by ID.

        Args:
            memory_id: The ID of the memory to delete.

        Returns:
            True if deletion succeeded, False if no API or not found.
        """
        if self.api is None:
            return False
        return await self.api.forget(memory_id)

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Notify memory of a completed turn (used for summarization or reflection).

        Args:
            user_content: The user message text.
            assistant_content: The assistant's response text.
            session_id: Optional session ID for scoped recording.
        """
        if self.api is not None:
            await self.api.on_turn_complete(user_content, assistant_content, session_id=session_id)

    async def on_session_end(self, messages: list[dict]) -> None:
        """Notify memory that a session has ended (cleanup, summarization).

        Args:
            messages: The full message history of the completed session.
        """
        if self.api is not None:
            await self.api.on_session_end(messages)

    async def on_pre_compact(self, messages: list[dict]) -> str:
        """Notify memory before context compaction (create summary if needed).

        Args:
            messages: The messages about to be compacted.

        Returns:
            Optional pre-compaction summary or metadata.
        """
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
        """Notify memory of a user writing to a memory file (e.g., MEMORY.md).

        Args:
            action: Action type ('create', 'update', 'delete', etc.).
            target: Target memory file or identifier.
            content: The content being written.
            metadata: Optional metadata about the write.
        """
        if self.api is not None:
            await self.api.on_memory_write(action, target, content, metadata)

    async def shutdown(self) -> None:
        """Clean up and close the memory provider API."""
        if self.api is not None:
            await self.api.shutdown()
