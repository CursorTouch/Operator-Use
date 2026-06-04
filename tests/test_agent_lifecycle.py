"""Agent lifecycle — phase guard, retry logic, invoke options, compaction trigger."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from helpers import FakeLLM, make_agent, text_seq, error_seq

from operator_use.agent.types import AgentConfig, PromptOptions
from operator_use.compaction.strategy.types import CompactionSettings
from operator_use.message.types import UserMessage


class TestAgentPhaseGuard:
    @pytest.mark.asyncio
    async def test_concurrent_invoke_raises(self):
        started = asyncio.Event()

        class SlowLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
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
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
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
        from operator_use.agent.service import Agent
        from operator_use.agent.types import AgentConfig
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.extension.types import LoadExtensionsResult
        from operator_use.resource.types import BaseResourceLoader
        from operator_use.session.manager import SessionManager
        from operator_use.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
        from operator_use.hooks.service import Hooks
        from pathlib import Path

        class FakeLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_tools(self): return []
            def get_commands(self): return []
            def get_hooks(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def get_soul_prompt(self): return None
            def get_user_profile(self): return None
            def get_agent_memory(self): return None
            def extend_resources(self, p): pass
            def get_subagent_profiles(self): return []
            def get_agent_profiles(self): return []
            def set_active_profile(self, profile): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        class _Null: pass

        attempt = [0]

        class RetryLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
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
        from operator_use.engine.service import Engine
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
    async def test_retry_context_clean_after_error(self):
        """Engine must not append error/abort turns to messages. If it did, retry
        attempt 2 would see a dangling assistant message and the provider would
        reject it with a role-order error (cascading 400s after an initial 429)."""
        from operator_use.agent.service import Agent
        from operator_use.agent.types import AgentConfig
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.extension.types import LoadExtensionsResult
        from operator_use.resource.types import BaseResourceLoader
        from operator_use.session.manager import SessionManager
        from operator_use.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
        from operator_use.hooks.service import Hooks
        from pathlib import Path

        class FakeLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_tools(self): return []
            def get_commands(self): return []
            def get_hooks(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def get_soul_prompt(self): return None
            def get_user_profile(self): return None
            def get_agent_memory(self): return None
            def extend_resources(self, paths): pass
            def get_subagent_profiles(self): return []
            def get_agent_profiles(self): return []
            def set_active_profile(self, profile): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        attempt = [0]
        seen_counts: list[int] = []

        class RecordingLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
            async def stream(self, ctx):
                attempt[0] += 1
                seen_counts.append(len(ctx.messages))
                if attempt[0] == 1:
                    for e in error_seq("transient"):
                        yield e
                else:
                    for e in text_seq("recovered"):
                        yield e
            async def invoke(self, ctx, thinking_level=None): return text_seq()

        hooks = Hooks()
        from operator_use.engine.service import Engine
        engine = Engine(llm=RecordingLLM(), tools=[], hooks=hooks)
        load_result = LoadExtensionsResult()
        config = AgentConfig(cwd=Path("/tmp"), retry_enabled=True, retry_max_retries=2, retry_base_delay_ms=0)
        agent = Agent(
            engine=engine,
            session_manager=SessionManager.in_memory(),
            resource_loader=FakeLoader(),
            extension_runtime=ExtensionRuntime(load_result, object(), hooks),
            compaction=Compaction(llm=RecordingLLM(), settings=CompactionSettings(enabled=False)),
            config=config,
        )
        agent._extensions = ExtensionRuntime(load_result, agent, hooks)

        await agent.invoke("go")
        assert attempt[0] == 2
        assert seen_counts[0] == seen_counts[1], (
            f"Retry received extra messages: attempt1={seen_counts[0]}, attempt2={seen_counts[1]}"
        )

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_runtime_error(self):
        from operator_use.agent.service import Agent
        from operator_use.agent.types import AgentConfig
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.extension.types import LoadExtensionsResult
        from operator_use.resource.types import BaseResourceLoader
        from operator_use.session.manager import SessionManager
        from operator_use.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
        from operator_use.hooks.service import Hooks
        from pathlib import Path

        class FakeLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_tools(self): return []
            def get_commands(self): return []
            def get_hooks(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def get_soul_prompt(self): return None
            def get_user_profile(self): return None
            def get_agent_memory(self): return None
            def extend_resources(self, p): pass
            def get_subagent_profiles(self): return []
            def get_agent_profiles(self): return []
            def set_active_profile(self, profile): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        class _Null: pass

        class AlwaysFailLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
            async def stream(self, ctx):
                for e in error_seq("permanent"):
                    yield e
            async def invoke(self, ctx, thinking_level=None): return error_seq()

        hooks = Hooks()
        sm = SessionManager.in_memory()
        from operator_use.engine.service import Engine
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
        for source in ("rpc", "cron", "subagent"):
            opts = PromptOptions(source=source)
            await agent.invoke("hello", opts)  # should not raise

    @pytest.mark.asyncio
    async def test_compaction_custom_instructions_accepted(self):
        agent, _ = make_agent(FakeLLM(text_seq()))
        opts = PromptOptions(compaction_custom_instructions="focus on code changes")
        await agent.invoke("hello", opts)  # should not raise
