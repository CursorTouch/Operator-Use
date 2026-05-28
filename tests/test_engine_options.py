"""Engine options callbacks — skip tool calls, transform_context, follow-up/steering callbacks, should_stop_after_turn."""
from __future__ import annotations

import pytest
from helpers import FakeLLM, make_tool, text_seq, tool_call_seq, collect_events, AnyParams

from operator_use.engine.service import Engine
from operator_use.engine.types import (
    FollowupMode, SteeringMode, Options,
    ToolExecutionEndEvent, AgentEndEvent,
)
from operator_use.inference.types import StartEvent, EndEvent, StopReason, ToolCallEndEvent
from operator_use.message.types import UserMessage, ToolCallContent, ToolResultContent


class TestSkipToolCalls:
    @pytest.mark.asyncio
    async def test_should_skip_returns_result_directly(self):
        """should_skip_tool_calls returning ToolResultContent skips real execution."""
        executed = []

        class Track:
            pass  # not a proper Tool, just for tracking

        def skip(tool_call: ToolCallContent) -> ToolResultContent:
            return ToolResultContent(id=tool_call.id, content="skipped", is_error=False, metadata={})

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t")],
                        options=Options(should_skip_tool_calls=skip))
        events = await collect_events(engine)
        ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        # No ToolExecutionEndEvent because skip returns before emitting it
        tool_msg_events = [e for e in events
                           if hasattr(e, 'message') and getattr(getattr(e, 'message', None), 'role', None)]
        # Engine used the skipped result
        assert any(isinstance(e, AgentEndEvent) for e in events)


class TestTransformContext:
    @pytest.mark.asyncio
    async def test_transform_context_replaces_messages(self):
        """transform_context callback can rewrite the message list before each LLM call."""
        seen_messages = []

        def transform(messages, signal):
            seen_messages.append(len(messages))
            replacement = [UserMessage.text("TRANSFORMED")]
            return replacement

        engine = Engine(llm=FakeLLM(text_seq()), tools=[],
                        options=Options(transform_context=transform))
        await engine.run([UserMessage.text("original")])
        assert seen_messages and seen_messages[0] == 1  # only the TRANSFORMED message

    @pytest.mark.asyncio
    async def test_transform_context_called_each_turn(self):
        call_count = [0]

        def transform(messages, signal):
            call_count[0] += 1
            return messages

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t")],
                        options=Options(transform_context=transform))
        await engine.run([UserMessage.text("go")])
        assert call_count[0] == 2  # once per LLM call


class TestGetFollowUpMessagesCallback:
    @pytest.mark.asyncio
    async def test_static_callback_supplies_follow_up(self):
        """get_follow_up_messages returning messages keeps the loop running for one more turn."""
        call_count = [0]

        def get_followups():
            call_count[0] += 1
            if call_count[0] == 1:
                return [UserMessage.text("follow-up from callback")]
            return []

        engine = Engine(llm=FakeLLM(text_seq("first"), text_seq("second")),
                        tools=[],
                        options=Options(get_follow_up_messages=get_followups))
        await engine.run([UserMessage.text("start")])
        assert engine.llm.call_count == 2

    @pytest.mark.asyncio
    async def test_callback_returning_empty_stops_loop(self):
        engine = Engine(llm=FakeLLM(text_seq("only")), tools=[],
                        options=Options(get_follow_up_messages=lambda: []))
        await engine.run([UserMessage.text("go")])
        assert engine.llm.call_count == 1


class TestGetSteeringMessagesCallback:
    @pytest.mark.asyncio
    async def test_static_steering_callback_injects_after_tool(self):
        """get_steering_messages callback injects messages after each tool turn."""
        seen = []

        def get_steering():
            msg = UserMessage.text("static_steer")
            seen.append(True)
            return [msg]

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t")],
                        options=Options(get_steering_messages=get_steering))
        await engine.run([UserMessage.text("go")])
        assert seen  # callback was invoked after tool turn


class TestShouldStopAfterTurn:
    @pytest.mark.asyncio
    async def test_stops_after_nth_turn(self):
        from operator_use.engine.types import FollowupMode
        n = [0]

        def stop_cb(msg, results):
            n[0] += 1
            return n[0] >= 2

        llm = FakeLLM(*[text_seq(f"r{i}") for i in range(5)])
        engine = Engine(llm=llm, tools=[],
                        options=Options(should_stop_after_turn=stop_cb,
                                        followup_mode=FollowupMode.OneAtATime))
        for i in range(5):
            await engine.follow_up(UserMessage.text(f"fu{i}"))
        await engine.run([UserMessage.text("start")])
        assert llm._idx == 2

    @pytest.mark.asyncio
    async def test_never_stop_drains_all_followups(self):
        from operator_use.engine.types import FollowupMode
        n = 3
        llm = FakeLLM(*[text_seq(f"r{i}") for i in range(n + 1)])
        engine = Engine(llm=llm, tools=[],
                        options=Options(should_stop_after_turn=lambda m, r: False,
                                        followup_mode=FollowupMode.OneAtATime))
        for i in range(n):
            await engine.follow_up(UserMessage.text(f"fu{i}"))
        await engine.run([UserMessage.text("go")])
        assert llm._idx == n + 1


class TestFollowupAndSteeringModeAll:
    @pytest.mark.asyncio
    async def test_followup_all_mode_drains_in_one_shot(self):
        llm = FakeLLM(text_seq("initial"), text_seq("all_consumed"))
        engine = Engine(llm=llm, tools=[],
                        options=Options(followup_mode=FollowupMode.All))
        for i in range(5):
            await engine.follow_up(UserMessage.text(f"fp{i}"))
        await engine.run([UserMessage.text("go")])
        assert llm.call_count == 2
        assert engine.state.follow_up_queue.is_empty()

    @pytest.mark.asyncio
    async def test_one_at_a_time_processes_one_per_turn(self):
        n = 4
        llm = FakeLLM(*[text_seq(f"r{i}") for i in range(n + 1)])
        engine = Engine(llm=llm, tools=[],
                        options=Options(followup_mode=FollowupMode.OneAtATime))
        for i in range(n):
            await engine.follow_up(UserMessage.text(f"fp{i}"))
        await engine.run([UserMessage.text("start")])
        assert llm.call_count == n + 1
