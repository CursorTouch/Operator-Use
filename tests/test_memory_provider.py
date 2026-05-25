from __future__ import annotations

import pytest

from program.memory import MemoryManager, MemoryRuntimeContext
from program.memory.api.mem0 import Mem0MemoryAPI
from program.memory.api.registry import MemoryAPIRegistry
from program.memory.api.supermemory import SupermemoryAPI
from program.memory.types import MemoryOptions
from program.memory.provider.registry import MemoryProviderRegistry


def test_memory_api_registry_loads_builtins():
    registry = MemoryAPIRegistry.from_builtins()

    assert registry.get("mem0") is Mem0MemoryAPI
    assert registry.get("supermemory") is SupermemoryAPI


def test_memory_provider_registry_loads_builtins():
    registry = MemoryProviderRegistry.from_builtins()

    assert registry.get("mem0") is not None
    assert registry.get("supermemory") is not None


def test_memory_manager_initializes_active_provider(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "user.md").write_text("User likes direct answers.", encoding="utf-8")

    manager = MemoryManager(provider_id=None)
    api = manager.initialize(MemoryRuntimeContext(project_memory_dir=memory_dir))

    assert api is None
    assert manager.search("direct") == []


class _FakeMem0Client:
    def __init__(self):
        self.add_calls = []

    def search(self, query, filters=None, top_k=5):
        return {
            "results": [
                {"id": "m1", "memory": f"Remember {query}", "score": 0.9},
            ]
        }

    def add(self, messages, **kwargs):
        self.add_calls.append((messages, kwargs))


@pytest.mark.asyncio
async def test_mem0_prefetch_and_sync_turn_with_fake_client():
    client = _FakeMem0Client()
    api = Mem0MemoryAPI(options=MemoryOptions(user_id="u1"), client=client)
    api.initialize(MemoryRuntimeContext(session_id="s1"))

    recalled = await api.prefetch("pytest")
    await api.sync_turn("use pytest", "ok", session_id="s1")

    assert "Relevant Mem0 memories" in recalled
    assert "Remember pytest" in recalled
    assert client.add_calls
    messages, kwargs = client.add_calls[0]
    assert messages[0]["role"] == "user"
    assert kwargs["user_id"] == "u1"
    assert kwargs["run_id"] == "s1"


class _FakeSupermemorySearch:
    def memories(self, **kwargs):
        return {
            "results": [
                {"id": "sm1", "content": f"Stored {kwargs['q']}", "score": 0.8},
            ]
        }


class _FakeSupermemoryClient:
    def __init__(self):
        self.search = _FakeSupermemorySearch()
        self.add_calls = []

    def add(self, **kwargs):
        self.add_calls.append(kwargs)


@pytest.mark.asyncio
async def test_supermemory_prefetch_and_sync_turn_with_fake_client():
    client = _FakeSupermemoryClient()
    api = SupermemoryAPI(options=MemoryOptions(user_id="u1"), client=client)
    api.initialize(MemoryRuntimeContext(session_id="s1"))

    recalled = await api.prefetch("gateway")
    await api.sync_turn("remember gateway", "stored", session_id="s1")

    assert "Relevant Supermemory memories" in recalled
    assert "Stored gateway" in recalled
    assert client.add_calls
    assert client.add_calls[0]["container_tag"] == "u1"
    assert "remember gateway" in client.add_calls[0]["content"]
