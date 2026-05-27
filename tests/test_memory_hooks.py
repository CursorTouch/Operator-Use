from __future__ import annotations

import pytest

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.manager import MemoryManager


class _HookAPI(BaseMemoryAPI):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        self.calls.append(("prefetch", query, session_id))
        return "recalled"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self.calls.append(("queue_prefetch", query, session_id))

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self.calls.append(("on_turn_complete", user_content, assistant_content, session_id))

    async def on_session_end(self, messages: list[dict]) -> None:
        self.calls.append(("on_session_end", messages))

    async def on_pre_compact(self, messages: list[dict]) -> str:
        self.calls.append(("on_pre_compact", messages))
        return "keep this"

    async def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None:
        self.calls.append(("on_memory_write", action, target, content, metadata))

    async def shutdown(self) -> None:
        self.calls.append(("shutdown",))


@pytest.mark.asyncio
async def test_memory_manager_forwards_lifecycle_hooks():
    api = _HookAPI()
    manager = MemoryManager(provider_id=None)
    manager.api = api

    recalled = await manager.prefetch("query", session_id="s1")
    manager.queue_prefetch("next", session_id="s1")
    await manager.on_turn_complete("user", "assistant", session_id="s1")
    await manager.on_session_end([{"role": "user", "content": "bye"}])
    compact = await manager.on_pre_compact([{"role": "assistant", "content": "summary"}])
    await manager.on_memory_write("remember", "target", "content", {"source": "test"})
    await manager.shutdown()

    assert recalled == "recalled"
    assert compact == "keep this"
    assert api.calls == [
        ("prefetch", "query", "s1"),
        ("queue_prefetch", "next", "s1"),
        ("on_turn_complete", "user", "assistant", "s1"),
        ("on_session_end", [{"role": "user", "content": "bye"}]),
        ("on_pre_compact", [{"role": "assistant", "content": "summary"}]),
        ("on_memory_write", "remember", "target", "content", {"source": "test"}),
        ("shutdown",),
    ]
