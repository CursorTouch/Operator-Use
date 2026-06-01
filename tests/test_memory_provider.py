from __future__ import annotations

import pytest

from operator_use.memory import MemoryManager, MemoryContext
from operator_use.memory.api.hindsight import HindsightMemoryAPI
from operator_use.memory.api.holographic import HolographicMemoryAPI
from operator_use.memory.api.mem0 import Mem0MemoryAPI
from operator_use.memory.api.openviking import OpenVikingMemoryAPI
from operator_use.memory.api.registry import MemoryAPIRegistry
from operator_use.memory.api.supermemory import SupermemoryAPI
from operator_use.memory.types import MemoryOptions
from operator_use.memory.provider.registry import MemoryProviderRegistry


def test_memory_api_registry_loads_builtins():
    registry = MemoryAPIRegistry.from_builtins()

    assert registry.get("mem0") is Mem0MemoryAPI
    assert registry.get("supermemory") is SupermemoryAPI
    assert registry.get("hindsight") is HindsightMemoryAPI
    assert registry.get("holographic") is HolographicMemoryAPI
    assert registry.get("openviking") is OpenVikingMemoryAPI


def test_memory_provider_registry_loads_builtins():
    registry = MemoryProviderRegistry.from_builtins()

    assert registry.get("mem0") is not None
    assert registry.get("supermemory") is not None
    assert registry.get("hindsight") is not None
    assert registry.get("holographic") is not None
    assert registry.get("openviking") is not None


def test_memory_manager_initializes_active_provider(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "user.md").write_text("User likes direct answers.", encoding="utf-8")

    manager = MemoryManager(provider_id=None)
    api = manager.initialize(MemoryContext(project_memory_dir=memory_dir))

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
async def test_mem0_prefetch_and_on_turn_complete_with_fake_client():
    client = _FakeMem0Client()
    api = Mem0MemoryAPI(options=MemoryOptions(user_id="u1"), client=client)
    api.initialize(MemoryContext(session_id="s1"))

    recalled = await api.prefetch("pytest")
    await api.on_turn_complete("use pytest", "ok", session_id="s1")

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
    from operator_use.memory.provider.types import MemoryProvider

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
    from operator_use.memory.api.base import BaseMemoryAPI

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
    from operator_use.memory.provider.types import MemoryProvider
    from operator_use.memory.api.base import BaseMemoryAPI

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
    api = manager.initialize(MemoryContext())
    assert isinstance(api, NullAPI)


def test_memory_manager_rejects_unknown_provider_id():
    manager = MemoryManager(provider_id="does-not-exist")
    with pytest.raises(ValueError, match="not found"):
        manager.initialize(MemoryContext())


def test_custom_provider_unregister():
    from operator_use.memory.provider.types import MemoryProvider

    registry = MemoryProviderRegistry.from_builtins()
    custom = MemoryProvider(id="temp", name="Temp", api="x", options=MemoryOptions())
    registry.register(custom)
    assert registry.get("temp") is not None

    registry.unregister("temp")
    assert registry.get("temp") is None
    # Builtins unaffected
    assert registry.get("mem0") is not None


class _FakeRecallResult:
    def __init__(self, text, type_="fact"):
        self.text = text
        self.type = type_


class _FakeRecallResponse:
    def __init__(self, results):
        self.results = results


class _FakeReflectResponse:
    def __init__(self, text):
        self.text = text


class _FakeHindsightClient:
    def __init__(self):
        self.retain_calls = []

    def recall(self, bank_id, query, budget=None, max_tokens=None):
        return _FakeRecallResponse([_FakeRecallResult(f"Recalled {query}")])

    def reflect(self, bank_id, query, budget=None):
        return _FakeReflectResponse(f"Synthesis about {query} for {bank_id}")

    def retain(self, bank_id, content, metadata=None):
        self.retain_calls.append({"bank_id": bank_id, "content": content, "metadata": metadata})


@pytest.mark.asyncio
async def test_hindsight_recall_prefetch_and_retain_with_fake_client():
    client = _FakeHindsightClient()
    api = HindsightMemoryAPI(options=MemoryOptions(config={"bank_id": "ops"}), client=client)
    api.initialize(MemoryContext(session_id="s1"))

    recalled = await api.prefetch("gateway")
    await api.on_turn_complete("remember gateway", "stored", session_id="s1")

    assert "Relevant Hindsight memories" in recalled
    assert "Recalled gateway" in recalled
    assert client.retain_calls
    assert client.retain_calls[0]["bank_id"] == "ops"
    assert "remember gateway" in client.retain_calls[0]["content"]
    assert client.retain_calls[0]["metadata"]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_hindsight_reflect_prefetch_and_reflect_method():
    client = _FakeHindsightClient()
    api = HindsightMemoryAPI(
        options=MemoryOptions(config={"bank_id": "ops", "prefetch_method": "reflect"}),
        client=client,
    )
    api.initialize(MemoryContext())

    recalled = await api.prefetch("Alice")
    synthesized = await api.reflect("Alice")

    assert "Hindsight synthesis" in recalled
    assert "Synthesis about Alice for ops" in recalled
    assert synthesized == "Synthesis about Alice for ops"


@pytest.mark.asyncio
async def test_holographic_store_search_forget_and_trust(tmp_path):
    api = HolographicMemoryAPI(options=MemoryOptions(config={"db_path": str(tmp_path / "holo.db")}))
    api.initialize(MemoryContext())

    mid = await api.remember("The deploy script lives in scripts/deploy.sh")
    await api.on_turn_complete("where are docs", "docs are in the docs/ directory", session_id="s1")

    results = api.search("deploy script", limit=5)
    assert results
    assert any("deploy.sh" in r.content for r in results)
    top = results[0]
    assert top.metadata["trust"] >= 0.5  # default trust, possibly reinforced

    # Recall reinforces trust: searching again raises the hit count.
    api.search("deploy script", limit=5)
    reinforced = api.search("deploy script", limit=5)[0]
    assert reinforced.metadata["hits"] >= 2

    assert await api.forget(mid) is True
    assert await api.forget(mid) is False
    await api.shutdown()


class _FakeVikingResource:
    def __init__(self, uri, score, l0):
        self.uri = uri
        self.score = score
        self.l0 = l0


class _FakeVikingFindResult:
    def __init__(self, resources):
        self.resources = resources


class _FakeVikingClient:
    def __init__(self):
        self.remembered = []
        self.initialized = False

    def initialize(self):
        self.initialized = True

    def find(self, query, target_uri=None):
        return _FakeVikingFindResult([
            _FakeVikingResource("viking://memory/oauth", 0.85, f"Summary about {query}"),
        ])

    def remember(self, content, target=None):
        self.remembered.append((content, target))
        return {"uri": "viking://memory/new"}


@pytest.mark.asyncio
async def test_openviking_prefetch_and_remember_with_fake_client():
    client = _FakeVikingClient()
    api = OpenVikingMemoryAPI(client=client)
    api.initialize(MemoryContext())

    recalled = await api.prefetch("oauth flow")
    await api.on_turn_complete("how does oauth work", "it uses tokens", session_id="s1")

    assert "OpenViking context" in recalled
    assert "Summary about oauth flow" in recalled
    assert client.remembered
    assert client.remembered[0][1] == "viking://memory/"


@pytest.mark.asyncio
async def test_supermemory_prefetch_and_on_turn_complete_with_fake_client():
    client = _FakeSupermemoryClient()
    api = SupermemoryAPI(options=MemoryOptions(user_id="u1"), client=client)
    api.initialize(MemoryContext(session_id="s1"))

    recalled = await api.prefetch("gateway")
    await api.on_turn_complete("remember gateway", "stored", session_id="s1")

    assert "Relevant Supermemory memories" in recalled
    assert "Stored gateway" in recalled
    assert client.add_calls
    assert client.add_calls[0]["container_tag"] == "u1"
    assert "remember gateway" in client.add_calls[0]["content"]
