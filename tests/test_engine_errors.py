"""Engine error paths, abort, run_continue edge cases, queue clearing."""
from __future__ import annotations

import asyncio

import pytest
from helpers import FakeLLM, make_tool, text_seq, tool_call_seq, error_seq, collect_events

from operator_use.engine.service import Engine
from operator_use.engine.types import (
    AgentEndEvent, AgentErrorEvent, SteeringMode, FollowupMode, Options,
    MessageEndEvent,
)
from operator_use.message.types import UserMessage, AssistantMessage, Role


class TestEngineErrorPaths:
    @pytest.mark.asyncio
    async def test_error_event_on_llm_error(self):
        engine = Engine(llm=FakeLLM(error_seq("boom")), tools=[])
        events = await collect_events(engine)
        assert any(isinstance(e, AgentErrorEvent) for e in events)
        assert any(isinstance(e, AgentEndEvent) for e in events)

    @pytest.mark.asyncio
    async def test_error_message_recorded_on_state(self):
        engine = Engine(llm=FakeLLM(error_seq("llm_down")), tools=[])
        await engine.run([UserMessage.text("go")])
        assert engine.state.error_message == "llm_down"

    @pytest.mark.asyncio
    async def test_reset_clears_error_state(self):
        engine = Engine(llm=FakeLLM(error_seq("fail"), text_seq("ok")), tools=[])
        await engine.run([UserMessage.text("go")])
        assert engine.state.error_message
        engine.reset()
        assert engine.state.error_message is None

    @pytest.mark.asyncio
    async def test_abort_stops_subsequent_turns(self):
        """Aborting during a tool execution prevents the next LLM turn from starting."""
        from helpers import AnyParams
        from operator_use.tool.types import Tool, ToolKind, ToolResult

        calls = []

        class CountLLM:
            def __init__(self): self._n = 0
            async def stream(self, context):
                self._n += 1
                calls.append(self._n)
                if self._n == 1:
                    for e in tool_call_seq("t1", "abort_me"):
                        yield e
                else:
                    for e in text_seq("second"):
                        yield e

        llm = CountLLM()
        engine_holder = []

        class AbortTool(Tool):
            async def execute(self, invocation, **kwargs):
                engine_holder[0].abort()
                return ToolResult(id=invocation.id, content="done", is_error=False, metadata={}, terminate=False)

        tool = AbortTool(name="abort_me", description="x", schema=AnyParams, kind=ToolKind.Read)
        engine = Engine(llm=llm, tools=[tool])
        engine_holder.append(engine)

        await engine.run([UserMessage.text("go")])
        assert len(calls) == 1  # abort during tool call → no second LLM call


class TestRunContinueEdgeCases:
    @pytest.mark.asyncio
    async def test_run_continue_after_error_and_reset(self):
        llm = FakeLLM(error_seq("oops"), text_seq("recovered"))
        engine = Engine(llm=llm, tools=[])
        await engine.run([UserMessage.text("start")])
        assert engine.state.error_message
        engine.reset()
        await engine.follow_up(UserMessage.text("retry"))
        await engine.run_continue()
        assert engine.state.error_message is None
        msgs = [m for m in engine.state.messages if m.role == Role.ASSISTANT]
        assert msgs[-1].text_content() == "recovered"

    @pytest.mark.asyncio
    async def test_run_continue_raises_when_already_streaming(self):
        engine = Engine(llm=FakeLLM(text_seq()), tools=[])
        engine.state.is_streaming = True
        with pytest.raises(RuntimeError, match="already processing"):
            await engine.run_continue()
        engine.state.is_streaming = False

    @pytest.mark.asyncio
    async def test_run_continue_with_follow_up_adds_turn(self):
        engine = Engine(llm=FakeLLM(text_seq("first"), text_seq("second")), tools=[])
        await engine.run([UserMessage.text("start")])
        count_after_first = len([m for m in engine.state.messages if m.role == Role.ASSISTANT])
        await engine.follow_up(UserMessage.text("follow"))
        await engine.run_continue()
        count_after_second = len([m for m in engine.state.messages if m.role == Role.ASSISTANT])
        assert count_after_second > count_after_first


class TestQueueManagement:
    @pytest.mark.asyncio
    async def test_clear_all_queues_empties_both(self):
        engine = Engine(llm=FakeLLM(text_seq()), tools=[])
        await engine.follow_up(UserMessage.text("fp"))
        await engine.steer(UserMessage.text("st"))
        assert engine.has_pending_messages()
        engine.clear_all_queues()
        assert not engine.has_pending_messages()

    @pytest.mark.asyncio
    async def test_clear_steering_leaves_followup(self):
        engine = Engine(llm=FakeLLM(text_seq()), tools=[])
        await engine.follow_up(UserMessage.text("fp"))
        await engine.steer(UserMessage.text("st"))
        engine.clear_steering()
        assert engine.state.steering_queue.is_empty()
        assert not engine.state.follow_up_queue.is_empty()

    @pytest.mark.asyncio
    async def test_clear_follow_up_leaves_steering(self):
        engine = Engine(llm=FakeLLM(text_seq()), tools=[])
        await engine.follow_up(UserMessage.text("fp"))
        await engine.steer(UserMessage.text("st"))
        engine.clear_follow_up()
        assert engine.state.follow_up_queue.is_empty()
        assert not engine.state.steering_queue.is_empty()

    @pytest.mark.asyncio
    async def test_heavy_interleave_no_deadlock(self):
        n = 15
        seqs = [tool_call_seq(f"tc{i}", "w") for i in range(n)] + [text_seq("done")]
        llm = FakeLLM(*seqs)
        engine = Engine(llm=llm, tools=[make_tool("w")],
                        options=Options(steering_mode=SteeringMode.OneAtATime))
        for i in range(n):
            await engine.steer(UserMessage.text(f"steer_{i}"))
        events = await collect_events(engine)
        assert any(isinstance(e, AgentEndEvent) for e in events)
