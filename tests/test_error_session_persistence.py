"""Tests for error message session persistence changes.

Three behaviours under test:
1. Engine emits the real AssistantMessage (not None) on error/abort.
2. strip_unusable_trailing_assistant strips error/abort messages from LLM context.
3. Agent writes the error to the session only on final failure, not on retries.
"""
from __future__ import annotations

import pytest
from helpers import FakeLLM, make_agent, error_seq, text_seq

from operator_use.agent.types import AgentConfig
from operator_use.engine.types import MessageEndEvent
from operator_use.inference.types import StopReason
from operator_use.message.types import AssistantMessage, TextContent, UserMessage
from operator_use.message.utils import strip_unusable_trailing_assistant
from operator_use.session.manager import SessionManager


# ---------------------------------------------------------------------------
# 1. Engine emits real message on error (not None)
# ---------------------------------------------------------------------------

class TestEngineEmitsRealMessageOnError:
    @pytest.mark.asyncio
    async def test_message_end_carries_real_message_on_error(self):
        from operator_use.agent.types import AgentContext
        from operator_use.engine.service import Engine

        engine = Engine(llm=FakeLLM(error_seq("network failure")), tools=[])

        message_end_messages = []
        async def capture(event):
            if isinstance(event, MessageEndEvent):
                message_end_messages.append(event.message)

        await engine.subscribe(capture)
        await engine.run(AgentContext(
            system_prompt="",
            messages=[UserMessage.text("hi")],
            tools=[],
        ))

        assert len(message_end_messages) == 1
        msg = message_end_messages[0]
        assert msg is not None, "MessageEndEvent.message must not be None on error"
        assert isinstance(msg, AssistantMessage)
        assert msg.stop_reason == StopReason.Error

    @pytest.mark.asyncio
    async def test_error_message_carries_error_text(self):
        from operator_use.agent.types import AgentContext
        from operator_use.engine.service import Engine

        engine = Engine(llm=FakeLLM(error_seq("rate_limit_exceeded")), tools=[])

        message_end_messages = []
        async def capture(event):
            if isinstance(event, MessageEndEvent):
                message_end_messages.append(event.message)

        await engine.subscribe(capture)
        await engine.run(AgentContext(
            system_prompt="",
            messages=[UserMessage.text("hi")],
            tools=[],
        ))

        msg = message_end_messages[0]
        assert msg.error == "rate_limit_exceeded"


# ---------------------------------------------------------------------------
# 2. strip_unusable_trailing_assistant filters error/abort messages
# ---------------------------------------------------------------------------

class TestStripUnusableTrailingAssistant:
    def _make_ok_msg(self, text: str = "hello") -> AssistantMessage:
        msg = AssistantMessage(contents=[TextContent(content=text)])
        msg.stop_reason = StopReason.Stop
        return msg

    def _make_error_msg(self, text: str = "") -> AssistantMessage:
        msg = AssistantMessage(contents=[TextContent(content=text)] if text else [])
        msg.stop_reason = StopReason.Error
        return msg

    def _make_abort_msg(self) -> AssistantMessage:
        msg = AssistantMessage(contents=[])
        msg.stop_reason = StopReason.Abort
        return msg

    def test_strips_error_message_with_no_content(self):
        msgs = [UserMessage.text("hi"), self._make_error_msg()]
        result = strip_unusable_trailing_assistant(msgs)
        assert len(result) == 1
        assert result[0].role.value == "user"

    def test_strips_error_message_even_with_partial_text(self):
        # Partial text from a streaming error should still be stripped
        msgs = [UserMessage.text("hi"), self._make_error_msg("I was about to")]
        result = strip_unusable_trailing_assistant(msgs)
        assert len(result) == 1

    def test_strips_abort_message(self):
        msgs = [UserMessage.text("hi"), self._make_abort_msg()]
        result = strip_unusable_trailing_assistant(msgs)
        assert len(result) == 1

    def test_keeps_successful_message_with_text(self):
        ok = self._make_ok_msg("done")
        msgs = [UserMessage.text("hi"), ok]
        result = strip_unusable_trailing_assistant(msgs)
        assert len(result) == 2
        assert result[-1] is ok

    def test_strips_error_but_keeps_prior_successful_turn(self):
        ok = self._make_ok_msg("first response")
        err = self._make_error_msg()
        user2 = UserMessage.text("go")
        msgs = [UserMessage.text("hi"), ok, user2, err]
        result = strip_unusable_trailing_assistant(msgs)
        # Strips the trailing error assistant, stops at the user message
        assert len(result) == 3
        assert result[-1] is user2
        assert result[-2] is ok

    def test_non_destructive_original_unchanged(self):
        err = self._make_error_msg()
        original = [UserMessage.text("hi"), err]
        strip_unusable_trailing_assistant(original)
        assert len(original) == 2  # original not mutated


# ---------------------------------------------------------------------------
# 3. Agent writes error to session only on final failure, not on retries
# ---------------------------------------------------------------------------

def make_retrying_agent(llm, max_retries: int):
    """make_agent variant with retry enabled."""
    from pathlib import Path
    from typing import cast
    from operator_use.agent.service import Agent
    from operator_use.compaction.strategy.summarization.service import SummarizationCompaction
    from operator_use.compaction.strategy.types import CompactionSettings, CompactionPreparation
    from operator_use.engine.service import Engine
    from operator_use.extension.runtime import ExtensionRuntime
    from operator_use.extension.types import ExtensionContext, LoadExtensionsResult
    from operator_use.hooks.service import Hooks
    from operator_use.message.types import AgentMessage as _AgentMessage
    from operator_use.resource.types import BaseResourceLoader

    CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": _AgentMessage})

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

    h = Hooks()
    sm = SessionManager.in_memory()
    engine = Engine(llm=llm, tools=[], hooks=h)
    load_result = LoadExtensionsResult()
    cs = CompactionSettings(enabled=False)
    config = AgentConfig(
        cwd=Path("/tmp"),
        retry_enabled=True,
        retry_max_retries=max_retries,
        retry_base_delay_ms=0,
    )
    agent = Agent(
        engine=engine,
        session_manager=sm,
        resource_loader=_FakeLoader(),
        extension_runtime=ExtensionRuntime(load_result, cast(ExtensionContext, None), h),
        compaction=SummarizationCompaction(llm=llm, settings=cs),
        config=config,
    )
    agent._extensions = ExtensionRuntime(load_result, agent, h)
    return agent, sm


def session_has_error_message(sm: SessionManager) -> bool:
    """Return True if any AssistantMessage in the session has stop_reason=Error."""
    for entry in sm.entries:
        from operator_use.session.types import MessageEntry
        if isinstance(entry, MessageEntry):
            msg = entry.message
            if isinstance(msg, AssistantMessage) and msg.stop_reason == StopReason.Error:
                return True
    return False


class TestAgentErrorSessionPersistence:
    @pytest.mark.asyncio
    async def test_final_error_is_written_to_session(self):
        """When retries are exhausted, the final error lands in the session."""
        agent, sm = make_retrying_agent(
            FakeLLM(error_seq("boom"), error_seq("boom")),
            max_retries=1,
        )
        with pytest.raises(RuntimeError, match="Agent failed"):
            await agent.invoke("hello")

        assert session_has_error_message(sm), \
            "Final error assistant message must be in the session"

    @pytest.mark.asyncio
    async def test_intermediate_retry_errors_not_written_to_session(self):
        """Retry attempts that eventually succeed leave no error message in the session."""
        agent, sm = make_retrying_agent(
            FakeLLM(error_seq("transient"), text_seq("all good")),
            max_retries=1,
        )
        await agent.invoke("hello")

        assert not session_has_error_message(sm), \
            "Intermediate retry error must not appear in the session on success"

    @pytest.mark.asyncio
    async def test_error_not_in_llm_context_on_next_turn(self):
        """After a final error, the error message is absent from the LLM context."""
        agent, sm = make_retrying_agent(
            FakeLLM(error_seq("fail"), text_seq("recovery")),
            max_retries=1,
        )
        # First call fails permanently (max_retries=0 for this scenario)
        agent2, sm2 = make_retrying_agent(
            FakeLLM(error_seq("fail"), text_seq("recovery")),
            max_retries=0,
        )
        with pytest.raises(RuntimeError):
            await agent2.invoke("first")

        # Second invoke — error msg must not appear in LLM context
        ctx_messages_seen = []
        original_run = agent2._engine.run

        async def capturing_run(ctx):
            ctx_messages_seen.extend(ctx.messages)
            await original_run(ctx)

        agent2._engine.run = capturing_run
        await agent2.invoke("second")

        error_turns = [
            m for m in ctx_messages_seen
            if isinstance(m, AssistantMessage) and m.stop_reason == StopReason.Error
        ]
        assert not error_turns, "Error assistant message must be stripped from LLM context"
