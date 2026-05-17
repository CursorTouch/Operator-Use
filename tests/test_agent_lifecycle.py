"""Agent lifecycle — phase guard, retry logic, invoke options, compaction trigger."""
from __future__ import annotations

import asyncio

import pytest
from helpers import FakeLLM, make_agent, text_seq, error_seq

from program.agent.types import AgentConfig, PromptOptions
from program.compaction.types import CompactionSettings
from program.message.types import UserMessage


class TestAgentPhaseGuard:
    @pytest.mark.asyncio
    async def test_concurrent_invoke_raises(self):
        started = asyncio.Event()

        class SlowLLM:
            async def stream(self, ctx):
                started.set()
                await asyncio.sleep(0.3)
                for e in text_seq("done"):
                    yield e
            async def invoke(self, ctx, thinking_level=None): return text_seq()

        agent, _ = make_agent(SlowLLM())
        task = asyncio.create_task(agent.invoke("first"))
        await started.wait()
        with pytest.raises(RuntimeError, match="busy"):
            await agent.invoke("second")
        await task

    @pytest.mark.asyncio
    async def test_run_compaction_raises_while_busy(self):
        started = asyncio.Event()

        class SlowLLM:
            async def stream(self, ctx):
                started.set()
                await asyncio.sleep(0.3)
                for e in text_seq("done"):
                    yield e
            async def invoke(self, ctx, thinking_level=None): return text_seq()

        agent, _ = make_agent(SlowLLM())
        task = asyncio.create_task(agent.invoke("hello"))
        await started.wait()
        with pytest.raises(RuntimeError, match="busy"):
            await agent.run_compaction()
        await task

    @pytest.mark.asyncio
    async def test_invoke_after_completion_is_allowed(self):
        agent, _ = make_agent(FakeLLM(text_seq("first"), text_seq("second")))
        await agent.invoke("turn1")
        await agent.invoke("turn2")  # should not raise


class TestAgentRetry:
    @pytest.mark.asyncio
    async def test_retry_recovers_from_transient_error(self):
        """With retry enabled, a transient error on first attempt retries successfully."""
        from program.agent.service import Agent
        from program.agent.types import AgentConfig
        from program.extension.runtime import ExtensionRuntime
        from program.extension.types import LoadExtensionsResult
        from program.resource.types import BaseResourceLoader
        from program.session.manager import SessionManager
        from program.compaction.compact import Compaction
        from program.hooks.service import Hooks
        from pathlib import Path

        class FakeLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_context_files(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def extend_resources(self, p): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        class _Null: pass

        attempt = [0]

        class RetryLLM:
            async def stream(self, ctx):
                attempt[0] += 1
                if attempt[0] == 1:
                    for e in error_seq("transient"):
                        yield e
                else:
                    for e in text_seq("recovered"):
                        yield e
            async def invoke(self, ctx, thinking_level=None): return text_seq()

        hooks = Hooks()
        sm = SessionManager.in_memory()
        from program.engine.service import Engine
        engine = Engine(llm=RetryLLM(), tools=[], hooks=hooks)
        load_result = LoadExtensionsResult()
        config = AgentConfig(cwd=Path("/tmp"), retry_enabled=True, retry_max_retries=2, retry_base_delay_ms=0)
        agent = Agent(
            engine=engine,
            session_manager=sm,
            resource_loader=FakeLoader(),
            extension_runtime=ExtensionRuntime(load_result, _Null(), hooks),
            compaction=Compaction(llm=RetryLLM(), settings=CompactionSettings(enabled=False)),
            config=config,
        )
        agent._extensions = ExtensionRuntime(load_result, agent, hooks)

        await agent.invoke("go")
        assert attempt[0] == 2  # failed once, succeeded on second

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_runtime_error(self):
        from program.agent.service import Agent
        from program.agent.types import AgentConfig
        from program.extension.runtime import ExtensionRuntime
        from program.extension.types import LoadExtensionsResult
        from program.resource.types import BaseResourceLoader
        from program.session.manager import SessionManager
        from program.compaction.compact import Compaction
        from program.hooks.service import Hooks
        from pathlib import Path

        class FakeLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_context_files(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def extend_resources(self, p): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        class _Null: pass

        class AlwaysFailLLM:
            async def stream(self, ctx):
                for e in error_seq("permanent"):
                    yield e
            async def invoke(self, ctx, thinking_level=None): return error_seq()

        hooks = Hooks()
        sm = SessionManager.in_memory()
        from program.engine.service import Engine
        engine = Engine(llm=AlwaysFailLLM(), tools=[], hooks=hooks)
        load_result = LoadExtensionsResult()
        config = AgentConfig(cwd=Path("/tmp"), retry_enabled=True, retry_max_retries=1, retry_base_delay_ms=0)
        agent = Agent(
            engine=engine,
            session_manager=sm,
            resource_loader=FakeLoader(),
            extension_runtime=ExtensionRuntime(load_result, _Null(), hooks),
            compaction=Compaction(llm=AlwaysFailLLM(), settings=CompactionSettings(enabled=False)),
            config=config,
        )
        agent._extensions = ExtensionRuntime(load_result, agent, hooks)

        with pytest.raises(RuntimeError, match="attempt"):
            await agent.invoke("go")


class TestAgentCompactionTrigger:
    @pytest.mark.asyncio
    async def test_run_compaction_returns_false_when_nothing_to_compact(self):
        agent, _ = make_agent(FakeLLM(text_seq()))
        result = await agent.run_compaction()
        assert result is False

    @pytest.mark.asyncio
    async def test_compact_request_flag_triggers_after_invoke(self):
        agent, _ = make_agent(FakeLLM(text_seq(), text_seq()),
                              compaction_settings=CompactionSettings(enabled=True))
        agent.compact()
        assert agent._compact_requested
        await agent.invoke("go")
        assert not agent._compact_requested  # consumed


class TestPromptOptions:
    @pytest.mark.asyncio
    async def test_source_field_accepted(self):
        agent, _ = make_agent(FakeLLM(text_seq()))
        opts = PromptOptions(source="rpc")
        await agent.invoke("hello", opts)  # should not raise

    @pytest.mark.asyncio
    async def test_compaction_custom_instructions_accepted(self):
        agent, _ = make_agent(FakeLLM(text_seq()))
        opts = PromptOptions(compaction_custom_instructions="focus on code changes")
        await agent.invoke("hello", opts)  # should not raise
