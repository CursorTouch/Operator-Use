"""Tests for MemoryManager wiring inside Agent.invoke() and _run_compaction()."""
from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from helpers import FakeLLM, text_seq, error_seq, make_agent

from operator_use.agent.service import Agent
from operator_use.agent.types import AgentConfig
from operator_use.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from operator_use.compaction.strategy.types import CompactionSettings
from operator_use.engine.service import Engine
from operator_use.extension.runtime import ExtensionRuntime
from operator_use.extension.types import ExtensionContext, LoadExtensionsResult
from operator_use.hooks.service import Hooks
from operator_use.resource.types import BaseResourceLoader
from operator_use.session.manager import SessionManager


# ---------------------------------------------------------------------------
# Fake MemoryManager — records calls without any real backend
# ---------------------------------------------------------------------------

class FakeMemory:
    """Tracks every call made by the Agent to the MemoryManager interface."""

    def __init__(self, prefetch_result: str = "recalled context"):
        self._prefetch_result = prefetch_result
        self.prefetch_calls: list[dict] = []
        self.queue_prefetch_calls: list[dict] = []
        self.on_turn_complete_calls: list[dict] = []
        self.on_pre_compact_calls: list[list] = []
        self.on_session_end_calls: list[list] = []
        self.shutdown_calls: int = 0
        self.api = SimpleNamespace()

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        self.prefetch_calls.append({"query": query, "session_id": session_id})
        return self._prefetch_result

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self.queue_prefetch_calls.append({"query": query, "session_id": session_id})

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self.on_turn_complete_calls.append({
            "user": user_content,
            "assistant": assistant_content,
            "session_id": session_id,
        })

    async def on_pre_compact(self, messages: list) -> str:
        self.on_pre_compact_calls.append(messages)
        return ""

    async def on_session_end(self, messages: list) -> None:
        self.on_session_end_calls.append(messages)

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


# ---------------------------------------------------------------------------
# Helper: build an Agent with a FakeMemory wired in
# ---------------------------------------------------------------------------

class _FakeLoader(BaseResourceLoader):
    def get_extensions(self): return LoadExtensionsResult()
    def get_skills(self): return [], []
    def get_tools(self): return []
    def get_commands(self): return []
    def get_hooks(self): return []
    def get_context_files(self): return []
    def get_system_prompt(self): return None
    def get_append_system_prompt(self): return []
    def get_soul_prompt(self): return None
    def get_user_profile(self): return None
    def get_agent_memory(self): return None
    def get_subagent_profiles(self): return []
    def get_agent_profiles(self): return []
    def set_active_profile(self, profile): pass
    def extend_resources(self, paths): pass
    def get_diagnostics(self, runtime=None): return []
    async def reload(self): pass


def make_agent_with_memory(
    llm,
    memory: FakeMemory,
    compaction_settings: CompactionSettings | None = None,
) -> tuple[Agent, SessionManager]:
    hooks = Hooks()
    sm = SessionManager.in_memory()
    engine = Engine(llm=llm, tools=[], hooks=hooks)
    load_result = LoadExtensionsResult()
    cs = compaction_settings or CompactionSettings(enabled=False)
    config = AgentConfig(cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0)
    agent = Agent(
        engine=engine,
        session_manager=sm,
        resource_loader=_FakeLoader(),
        extension_runtime=ExtensionRuntime(load_result, cast(ExtensionContext, None), hooks),
        compaction=Compaction(llm=llm, settings=cs),
        config=config,
        memory_manager=memory,  # type: ignore[arg-type]
    )
    agent._extensions = ExtensionRuntime(load_result, agent, hooks)
    return agent, sm


# ---------------------------------------------------------------------------
# Tests: prefetch
# ---------------------------------------------------------------------------

class TestMemoryPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_called_once_per_invoke(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("hi")), mem)
        await agent.invoke("hello")
        assert len(mem.prefetch_calls) == 1

    @pytest.mark.asyncio
    async def test_prefetch_receives_user_input(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("hi")), mem)
        await agent.invoke("what is the capital of France?")
        assert mem.prefetch_calls[0]["query"] == "what is the capital of France?"

    @pytest.mark.asyncio
    async def test_prefetch_result_injected_into_context_only_user_message(self):
        mem = FakeMemory(prefetch_result="Paris is the capital")
        llm = FakeLLM(text_seq("hi"))
        agent, sm = make_agent_with_memory(llm, mem)
        await agent.invoke("capital question")
        assert "<memory>" not in agent.get_system_prompt()
        sent_user = llm.contexts[0].messages[-1]
        sent_text = sent_user.contents[0].content
        assert "<memory>" in sent_text
        assert "Paris is the capital" in sent_text
        persisted_user = sm.build_session_context().messages[0]
        assert persisted_user.contents[0].content == "capital question"

    @pytest.mark.asyncio
    async def test_empty_prefetch_produces_no_memory_block(self):
        mem = FakeMemory(prefetch_result="")
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("hi")), mem)
        await agent.invoke("hello")
        assert "<memory>" not in agent.get_system_prompt()

    @pytest.mark.asyncio
    async def test_no_memory_manager_skips_prefetch(self):
        # make_agent from helpers wires no memory_manager
        agent, _ = make_agent(FakeLLM(text_seq("hi")))
        # Should complete without errors — no memory attached
        await agent.invoke("hello")
        # No memory manager attached — internal attribute stays None
        assert agent._memory_manager is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests: on_turn_complete
# ---------------------------------------------------------------------------

class TestMemorySyncTurn:
    @pytest.mark.asyncio
    async def test_on_turn_complete_called_after_successful_invoke(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("response text")), mem)
        await agent.invoke("user input")
        assert len(mem.on_turn_complete_calls) == 1

    @pytest.mark.asyncio
    async def test_on_turn_complete_receives_correct_user_input(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("reply")), mem)
        await agent.invoke("my question")
        assert mem.on_turn_complete_calls[0]["user"] == "my question"

    @pytest.mark.asyncio
    async def test_on_turn_complete_receives_assistant_text(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("assistant reply")), mem)
        await agent.invoke("ping")
        # Assistant text should appear somewhere in the recorded value
        assert "assistant reply" in mem.on_turn_complete_calls[0]["assistant"]

    @pytest.mark.asyncio
    async def test_on_turn_complete_not_called_after_error(self):
        """If the agent fails (no retry), on_turn_complete must not be called."""
        mem = FakeMemory()
        llm = FakeLLM(error_seq("boom"))
        hooks = Hooks()
        sm = SessionManager.in_memory()
        engine = Engine(llm=llm, tools=[], hooks=hooks)
        load_result = LoadExtensionsResult()
        config = AgentConfig(cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0)
        agent = Agent(
            engine=engine,
            session_manager=sm,
            resource_loader=_FakeLoader(),
            extension_runtime=ExtensionRuntime(load_result, cast(ExtensionContext, None), hooks),
            compaction=Compaction(llm=llm, settings=CompactionSettings(enabled=False)),
            config=config,
            memory_manager=mem,  # type: ignore[arg-type]
        )
        agent._extensions = ExtensionRuntime(load_result, agent, hooks)

        with pytest.raises(RuntimeError):
            await agent.invoke("fail")

        assert len(mem.on_turn_complete_calls) == 0

    @pytest.mark.asyncio
    async def test_on_turn_complete_called_per_invoke(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(
            FakeLLM(text_seq("first"), text_seq("second")), mem
        )
        await agent.invoke("turn one")
        await agent.invoke("turn two")
        assert len(mem.on_turn_complete_calls) == 2


# ---------------------------------------------------------------------------
# Tests: queue_prefetch (cache warm-up)
# ---------------------------------------------------------------------------

class TestMemoryQueuePrefetch:
    @pytest.mark.asyncio
    async def test_queue_prefetch_called_after_successful_invoke(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("assistant reply")), mem)
        await agent.invoke("user input")
        assert len(mem.queue_prefetch_calls) == 1

    @pytest.mark.asyncio
    async def test_queue_prefetch_uses_assistant_text_as_hint(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("assistant reply")), mem)
        await agent.invoke("ping")
        assert "assistant reply" in mem.queue_prefetch_calls[0]["query"]

    @pytest.mark.asyncio
    async def test_queue_prefetch_not_called_after_error(self):
        mem = FakeMemory()
        llm = FakeLLM(error_seq("boom"))
        hooks = Hooks()
        sm = SessionManager.in_memory()
        engine = Engine(llm=llm, tools=[], hooks=hooks)
        load_result = LoadExtensionsResult()
        config = AgentConfig(cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0)
        agent = Agent(
            engine=engine,
            session_manager=sm,
            resource_loader=_FakeLoader(),
            extension_runtime=ExtensionRuntime(load_result, cast(ExtensionContext, None), hooks),
            compaction=Compaction(llm=llm, settings=CompactionSettings(enabled=False)),
            config=config,
            memory_manager=mem,  # type: ignore[arg-type]
        )
        agent._extensions = ExtensionRuntime(load_result, agent, hooks)

        with pytest.raises(RuntimeError):
            await agent.invoke("fail")

        assert len(mem.queue_prefetch_calls) == 0

    @pytest.mark.asyncio
    async def test_queue_prefetch_called_once_per_invoke(self):
        mem = FakeMemory()
        agent, _ = make_agent_with_memory(
            FakeLLM(text_seq("a"), text_seq("b")), mem
        )
        await agent.invoke("first")
        await agent.invoke("second")
        assert len(mem.queue_prefetch_calls) == 2


# ---------------------------------------------------------------------------
# Tests: on_pre_compact
# ---------------------------------------------------------------------------

class TestMemoryOnPreCompact:
    @pytest.mark.asyncio
    async def test_on_pre_compact_called_before_compaction(self):
        """on_pre_compact must fire when run_compaction() is invoked explicitly."""
        from operator_use.compaction.strategy.types import CompactionResult
        from unittest.mock import AsyncMock, patch

        mem = FakeMemory()
        cs = CompactionSettings(enabled=True, reserve_tokens=0, keep_recent_tokens=0)
        llm = FakeLLM(text_seq("hi"))
        agent, sm = make_agent_with_memory(llm, mem, compaction_settings=cs)

        # Patch _compaction.prepare + compact so we don't need real token budgets
        fake_result = CompactionResult(
            summary="summary",
            retained_from_id="entry-1",
            tokens_before=100,
            details={},
        )

        # Seed a message so prepare() returns something non-None
        await agent.invoke("hello")
        mem.on_pre_compact_calls.clear()  # reset; invoke may not trigger compaction

        # Drive compaction directly
        with patch.object(agent._compaction, "prepare", return_value=SimpleNamespace(
            branch_entries=[], summary_target_tokens=100
        )):
            with patch.object(agent._compaction, "compact", new=AsyncMock(return_value=fake_result)):
                await agent.run_compaction()

        assert len(mem.on_pre_compact_calls) == 1

    @pytest.mark.asyncio
    async def test_on_pre_compact_not_called_when_prepare_returns_none(self):
        """If prepare() returns None, compaction is skipped and on_pre_compact is not called."""
        mem = FakeMemory()
        cs = CompactionSettings(enabled=True)
        llm = FakeLLM(text_seq("hi"))
        agent, _ = make_agent_with_memory(llm, mem, compaction_settings=cs)

        # Prepare returns None when session has no content worth compacting
        from unittest.mock import patch
        with patch.object(agent._compaction, "prepare", return_value=None):
            result = await agent.run_compaction()

        assert result is False
        assert len(mem.on_pre_compact_calls) == 0


# ---------------------------------------------------------------------------
# Tests: no memory manager (guard against NoneType errors)
# ---------------------------------------------------------------------------

class TestNoMemoryManager:
    @pytest.mark.asyncio
    async def test_invoke_without_memory_completes_normally(self):
        agent, _ = make_agent(FakeLLM(text_seq("hello")))
        await agent.invoke("hi")  # no errors

    @pytest.mark.asyncio
    async def test_multiple_invokes_without_memory(self):
        agent, _ = make_agent(FakeLLM(text_seq("a"), text_seq("b")))
        await agent.invoke("first")
        await agent.invoke("second")


# ---------------------------------------------------------------------------
# Tests: manager present but no provider configured (api=None)
# ---------------------------------------------------------------------------

class TestMemoryManagerNoProvider:
    """MemoryManager exists but initialize() was never called (no provider_id)."""

    def _make_unprovided_manager(self) -> FakeMemory:
        from operator_use.memory.manager import MemoryManager
        return MemoryManager()  # type: ignore[return-value]

    @pytest.mark.asyncio
    async def test_prefetch_returns_empty_string(self):
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        result = await m.prefetch("anything")
        assert result == ""

    @pytest.mark.asyncio
    async def test_queue_prefetch_is_a_no_op(self):
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        m.queue_prefetch("anything")  # must not raise

    @pytest.mark.asyncio
    async def test_on_turn_complete_is_a_no_op(self):
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        await m.on_turn_complete("user", "assistant")  # must not raise

    @pytest.mark.asyncio
    async def test_on_pre_compact_returns_empty_string(self):
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        result = await m.on_pre_compact([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_invoke_with_unprovided_manager_completes_normally(self):
        """Agent with a MemoryManager that has no backend must not raise."""
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("hi")), m)  # type: ignore[arg-type]
        await agent.invoke("hello")

    @pytest.mark.asyncio
    async def test_no_memory_block_injected_when_prefetch_returns_empty(self):
        from operator_use.memory.manager import MemoryManager
        m = MemoryManager()
        agent, _ = make_agent_with_memory(FakeLLM(text_seq("hi")), m)  # type: ignore[arg-type]
        await agent.invoke("hello")
        assert "<memory>" not in agent.get_system_prompt()
