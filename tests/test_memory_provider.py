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


def test_custom_memory_provider_registerable():
    from program.memory.provider.types import MemoryProvider

    registry = MemoryProviderRegistry.from_builtins()
    custom = MemoryProvider(
        id="custom-db",
        name="Custom DB",
        api="custom_db_api",
        options=MemoryOptions(),
    )
    registry.register(custom)

    assert registry.get("custom-db") is not None
    assert registry.get("custom-db").name == "Custom DB"
    # Builtins still intact
    assert registry.get("mem0") is not None


def test_custom_memory_api_registerable():
    from program.memory.api.base import BaseMemoryAPI

    class MyCustomAPI(BaseMemoryAPI):
        async def prefetch(self, query, *, session_id=""):
            return f"custom:{query}"

    registry = MemoryAPIRegistry.from_builtins()
    registry.register("custom_db_api", MyCustomAPI)

    resolved = registry.get("custom_db_api")
    assert resolved is MyCustomAPI
    # Builtins still intact
    assert registry.get("mem0") is Mem0MemoryAPI


def test_memory_manager_uses_custom_registry(tmp_path):
    from program.memory.provider.types import MemoryProvider
    from program.memory.api.base import BaseMemoryAPI

    class NullAPI(BaseMemoryAPI):
        pass

    provider_registry = MemoryProviderRegistry.from_builtins()
    api_registry = MemoryAPIRegistry.from_builtins()

    provider_registry.register(MemoryProvider(
        id="null-mem",
        name="Null Memory",
        api="null_api",
        options=MemoryOptions(),
    ))
    api_registry.register("null_api", NullAPI)

    manager = MemoryManager(
        provider_id="null-mem",
        providers=provider_registry,
        apis=api_registry,
    )
    api = manager.initialize(MemoryRuntimeContext())
    assert isinstance(api, NullAPI)


def test_memory_manager_rejects_unknown_provider_id():
    manager = MemoryManager(provider_id="does-not-exist")
    with pytest.raises(ValueError, match="not found"):
        manager.initialize(MemoryRuntimeContext())


def test_custom_provider_unregister():
    from program.memory.provider.types import MemoryProvider

    registry = MemoryProviderRegistry.from_builtins()
    custom = MemoryProvider(id="temp", name="Temp", api="x", options=MemoryOptions())
    registry.register(custom)
    assert registry.get("temp") is not None

    registry.unregister("temp")
    assert registry.get("temp") is None
    # Builtins unaffected
    assert registry.get("mem0") is not None


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
