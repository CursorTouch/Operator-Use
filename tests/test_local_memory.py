from __future__ import annotations

import json

import pytest

from operator_use.memory.api.local import (
    LocalMemoryAPI,
    _overlap_score,
    _tokenize,
)
from operator_use.memory.types import MemoryContext, MemoryOptions


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_api(tmp_path, options=None):
    api = LocalMemoryAPI(options=options)
    api.initialize(MemoryContext(project_memory_dir=tmp_path))
    return api


def _read_store(tmp_path):
    path = tmp_path / "memories.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ── unit: scoring helpers ─────────────────────────────────────────────────────

def test_tokenize_lowercases_and_splits():
    assert _tokenize("Hello World") == {"hello", "world"}


def test_tokenize_ignores_punctuation():
    assert _tokenize("foo, bar!") == {"foo", "bar"}


def test_overlap_score_identical():
    tokens = _tokenize("python asyncio")
    assert _overlap_score(tokens, tokens) == 1.0


def test_overlap_score_disjoint():
    assert _overlap_score(_tokenize("alpha"), _tokenize("beta")) == 0.0


def test_overlap_score_partial():
    score = _overlap_score(_tokenize("foo bar"), _tokenize("foo baz"))
    assert 0.0 < score < 1.0


def test_overlap_score_empty():
    assert _overlap_score(set(), _tokenize("anything")) == 0.0


# ── initialization ────────────────────────────────────────────────────────────

def test_initialize_creates_store_dir(tmp_path):
    api = _make_api(tmp_path)
    assert api._store_path == tmp_path / "memories.jsonl"
    assert api._store_path.parent.exists()


def test_initialize_without_profile_raises():
    api = LocalMemoryAPI()
    with pytest.raises(ValueError, match="profile"):
        api.initialize(MemoryContext())


def test_options_root_dir_takes_precedence(tmp_path):
    override = tmp_path / "custom"
    override.mkdir()
    api = LocalMemoryAPI(options=MemoryOptions(root_dir=override))
    api.initialize(MemoryContext(project_memory_dir=tmp_path / "profile"))
    assert api._store_path == override / "memories.jsonl"


# ── remember / forget ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_appends_entry(tmp_path):
    api = _make_api(tmp_path)
    entry_id = await api.remember("The sky is blue")
    entries = _read_store(tmp_path)
    assert len(entries) == 1
    assert entries[0]["content"] == "The sky is blue"
    assert entries[0]["id"] == entry_id
    assert entries[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_remember_empty_string_is_noop(tmp_path):
    api = _make_api(tmp_path)
    result = await api.remember("   ")
    assert result == ""
    assert _read_store(tmp_path) == []


@pytest.mark.asyncio
async def test_forget_removes_entry(tmp_path):
    api = _make_api(tmp_path)
    id1 = await api.remember("Fact A")
    id2 = await api.remember("Fact B")
    removed = await api.forget(id1)
    assert removed is True
    entries = _read_store(tmp_path)
    assert len(entries) == 1
    assert entries[0]["id"] == id2


@pytest.mark.asyncio
async def test_forget_unknown_id_returns_false(tmp_path):
    api = _make_api(tmp_path)
    await api.remember("some fact")
    assert await api.forget("nonexistent-id") is False


# ── search ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_returns_matching_entries(tmp_path):
    api = _make_api(tmp_path)
    await api.remember("Python is a programming language")
    await api.remember("The Eiffel Tower is in Paris")
    results = api.search("Python programming")
    assert len(results) == 1
    assert "Python" in results[0].content


@pytest.mark.asyncio
async def test_search_returns_empty_for_no_match(tmp_path):
    api = _make_api(tmp_path)
    await api.remember("The Eiffel Tower is in Paris")
    assert api.search("quantum physics") == []


@pytest.mark.asyncio
async def test_search_respects_limit(tmp_path):
    api = _make_api(tmp_path)
    for i in range(10):
        await api.remember(f"fact number {i} about testing")
    results = api.search("fact testing", limit=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_search_empty_store_returns_empty(tmp_path):
    api = _make_api(tmp_path)
    assert api.search("anything") == []


# ── prefetch ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prefetch_returns_memory_block(tmp_path):
    api = _make_api(tmp_path)
    await api.remember("asyncio is used for async Python code")
    result = await api.prefetch("asyncio coroutines")
    assert "## Recalled Memory" in result
    assert "asyncio" in result


@pytest.mark.asyncio
async def test_prefetch_empty_store_returns_empty_string(tmp_path):
    api = _make_api(tmp_path)
    result = await api.prefetch("anything")
    assert result == ""


@pytest.mark.asyncio
async def test_prefetch_disabled_via_options(tmp_path):
    api = _make_api(tmp_path, options=MemoryOptions(prefetch=False))
    await api.remember("some important fact")
    result = await api.prefetch("important")
    assert result == ""


# ── on_turn_complete with fake LLM ────────────────────────────────────────────

class _FakeLLM:
    """Returns a fixed text response from invoke()."""

    def __init__(self, text: str):
        self._text = text

    async def invoke(self, context):
        from dataclasses import dataclass
        from operator_use.message.types import TextContent

        @dataclass
        class _Data:
            text: TextContent

        @dataclass
        class _Event:
            data: _Data

        return [_Event(data=_Data(text=TextContent(content=self._text)))]


@pytest.mark.asyncio
async def test_on_turn_complete_stores_extracted_facts(tmp_path):
    llm = _FakeLLM("User prefers concise answers\nUser works with Python daily")
    api = _make_api(tmp_path)
    api.context.llm = llm

    await api.on_turn_complete("Keep it short please", "Sure!", session_id="s1")

    entries = _read_store(tmp_path)
    assert len(entries) == 2
    assert entries[0]["source"] == "turn"
    assert entries[0]["session_id"] == "s1"
    assert "concise" in entries[0]["content"]


@pytest.mark.asyncio
async def test_on_turn_complete_none_response_stores_nothing(tmp_path):
    llm = _FakeLLM("NONE")
    api = _make_api(tmp_path)
    api.context.llm = llm

    await api.on_turn_complete("hi", "hello", session_id="s1")
    assert _read_store(tmp_path) == []


@pytest.mark.asyncio
async def test_on_turn_complete_no_llm_stores_nothing(tmp_path):
    api = _make_api(tmp_path)
    # context.llm is None by default
    await api.on_turn_complete("user msg", "assistant msg", session_id="s1")
    assert _read_store(tmp_path) == []


@pytest.mark.asyncio
async def test_on_turn_complete_disabled_sync_turns(tmp_path):
    llm = _FakeLLM("some fact")
    api = _make_api(tmp_path, options=MemoryOptions(sync_turns=False))
    api.context.llm = llm
    await api.on_turn_complete("msg", "reply")
    assert _read_store(tmp_path) == []


# ── registry ──────────────────────────────────────────────────────────────────

def test_local_api_registered_in_builtins():
    from operator_use.memory.api.registry import MemoryAPIRegistry
    registry = MemoryAPIRegistry.from_builtins()
    assert registry.get("local") is LocalMemoryAPI


def test_local_provider_registered_in_builtins():
    from operator_use.memory.provider.registry import MemoryProviderRegistry
    registry = MemoryProviderRegistry.from_builtins()
    provider = registry.get("local")
    assert provider is not None
    assert provider.api == "local"
