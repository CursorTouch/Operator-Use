from __future__ import annotations

from abc import ABC
from typing import Any

from operator_use.memory.types import MemoryOptions, MemoryContext, MemorySearchResult


class BaseMemoryAPI(ABC):
    """Base class for memory backends.

    Memory APIs should keep external latency off the critical path where possible.
    Slow providers can cache recall in ``queue_prefetch`` and return it from
    ``prefetch`` on the next turn.
    """

    def __init__(self, options: MemoryOptions | None = None) -> None:
        self.options = options or MemoryOptions()
        self.context = MemoryContext()

    def initialize(self, context: MemoryContext) -> None:
        self.context = context

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        return None

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        return None

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        return []

    async def reflect(self, query: str, *, session_id: str = "") -> str:
        """Synthesize an answer across stored memories. Empty if unsupported."""
        return ""

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        await self.on_memory_write("remember", "", content, metadata)
        return ""

    async def forget(self, memory_id: str) -> bool:
        return False

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    async def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        raise NotImplementedError(f"Memory API {type(self).__name__} does not handle tool {name}")

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        return None

    async def on_pre_compact(self, messages: list[dict[str, Any]]) -> str:
        return ""

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def shutdown(self) -> None:
        return None
