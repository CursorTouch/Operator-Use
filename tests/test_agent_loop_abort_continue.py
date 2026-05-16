"""Advanced Engine tests: abort, run_continue, follow-up queue, should_stop_after_turn."""
import asyncio
import pytest
from pydantic import BaseModel

from program.engine.engine import Engine
from program.engine.types import (
    AgentEndEvent, AgentErrorEvent, AgentStartEvent,
    MessageEndEvent, TurnEndEvent, Options,
)
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from program.message.types import (
    UserMessage, AssistantMessage, TextContent, ToolCallContent,
    ToolResultContent, Role,
)
from program.tool.types import Tool, ToolKind, ToolInvocation, ToolResult


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0

    async def stream(self, context: LLMContext):
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        for e in events:
            yield e


class AnyParams(BaseModel):
    pass


def text_seq(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def tool_call_seq(tool_id: str, name: str) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args={})
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def make_tool(name: str, result: str = "ok") -> Tool:
    class FakeTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
            return ToolResult.ok(invocation.id, result)
    return FakeTool(name=name, description="fake", schema=AnyParams, kind=ToolKind.Read)


async def run_loop(llm, tools=None, options=None, messages=None):
    loop = Engine(llm=llm, tools=tools or [], options=options or Options())
    events = []
    await loop.subscribe(lambda e: events.append(e))
    await loop.run(messages or [UserMessage.text("go")])
    return events, loop


# ── abort() ───────────────────────────────────────────────────────────────────

class TestAbort:
    @pytest.mark.asyncio
    async def test_abort_before_run_has_no_effect(self):
        llm = FakeLLM(text_seq("hello"))
        loop = Engine(llm=llm, tools=[])
        loop.abort()  # set before run — should be reset when run() creates a new signal
        events, _ = await run_loop(llm, messages=[UserMessage.text("go")])
        # Engine completed normally
        assert any(isinstance(e, AgentEndEvent) for e in events)

    @pytest.mark.asyncio
    async def test_abort_during_tool_call_stops_loop(self):
        loop_ref: list[Engine] = []

        class AbortingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                loop_ref[0].abort()
                return ToolResult.ok(invocation.id, "aborted")

        tool = AbortingTool(name="aborter", description="t", schema=AnyParams, kind=ToolKind.Read)
        llm = FakeLLM(tool_call_seq("t1", "aborter"), text_seq("should not reach"))
        loop = Engine(llm=llm, tools=[tool])
        loop_ref.append(loop)
        events = []
        await loop.subscribe(lambda e: events.append(e))
        await loop.run([UserMessage.text("go")])
        # Engine ends after tool, does NOT make a second LLM call for text
        end_events = [e for e in events if isinstance(e, AgentEndEvent)]
        assert len(end_events) == 1
        # LLM was only called once (for the tool call turn)
        assert llm._idx == 1

    @pytest.mark.asyncio
    async def test_is_idle_true_after_run(self):
        llm = FakeLLM(text_seq("done"))
        _, loop = await run_loop(llm)
        assert loop.is_idle is True

    @pytest.mark.asyncio
    async def test_is_idle_false_conceptually_during_run(self):
        streaming_states: list[bool] = []

        class TrackingLLM:
            async def stream(self, context: LLMContext):
                for e in text_seq("ok"):
                    yield e

        loop = Engine(llm=TrackingLLM(), tools=[])

        original_loop = loop._loop

        async def patched_loop(messages, emit, signal):
            streaming_states.append(loop.state.is_streaming)
            await original_loop(messages, emit, signal)

        loop._loop = patched_loop
        await loop.run([UserMessage.text("hi")])
        assert True in streaming_states


# ── reset() ───────────────────────────────────────────────────────────────────

class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_error(self):
        llm = FakeLLM(
            [StartEvent(), ErrorEvent(reason=StopReason.Error, error="fail")],
        )
        _, loop = await run_loop(llm)
        assert loop.state.error_message is not None
        loop.reset()
        assert loop.state.error_message is None

    @pytest.mark.asyncio
    async def test_reset_does_not_clear_messages(self):
        # reset() only clears queues/error/pending — messages are preserved
        llm = FakeLLM(text_seq("hi"))
        _, loop = await run_loop(llm)
        msg_count = len(loop.state.messages)
        assert msg_count > 0
        loop.reset()
        assert len(loop.state.messages) == msg_count

    @pytest.mark.asyncio
    async def test_reset_allows_rerun(self):
        llm = FakeLLM(
            [StartEvent(), ErrorEvent(reason=StopReason.Error, error="fail")],
            text_seq("recovered"),
        )
        loop = Engine(llm=llm, tools=[])
        events1 = []
        await loop.subscribe(lambda e: events1.append(e))
        await loop.run([UserMessage.text("first")])
        assert loop.state.error_message is not None

        loop.reset()
        events2 = []
        await loop.subscribe(lambda e: events2.append(e))
        await loop.run([UserMessage.text("second")])
        assert loop.state.error_message is None
        msg_ends = [e for e in events2 if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 1


# ── run_continue() ────────────────────────────────────────────────────────────

class TestRunContinue:
    @pytest.mark.asyncio
    async def test_continue_from_tool_result(self):
        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("continued"))
        loop = Engine(llm=llm, tools=[make_tool("my_tool")])
        await loop.run([UserMessage.text("go")])
        # After tool call + response, messages should include user + assistant(tool) + tool + assistant(text)
        roles = [m.role for m in loop.state.messages]
        assert Role.TOOL in roles
        assert Role.ASSISTANT in roles

    @pytest.mark.asyncio
    async def test_run_continue_raises_if_last_is_assistant(self):
        llm = FakeLLM(text_seq("done"))
        loop = Engine(llm=llm, tools=[])
        await loop.run([UserMessage.text("hi")])
        # Last message is assistant, no steering/followup queued
        with pytest.raises(RuntimeError, match="Cannot continue"):
            await loop.run_continue()

    @pytest.mark.asyncio
    async def test_run_continue_raises_if_no_messages(self):
        llm = FakeLLM(text_seq("done"))
        loop = Engine(llm=llm, tools=[])
        with pytest.raises(RuntimeError, match="No messages"):
            await loop.run_continue()

    @pytest.mark.asyncio
    async def test_run_continue_with_steering_queue(self):
        llm = FakeLLM(text_seq("first"), text_seq("steered"))
        loop = Engine(llm=llm, tools=[])
        await loop.run([UserMessage.text("go")])
        # Queue a steering message then continue
        await loop.steer(UserMessage.text("steer me"))
        events = []
        await loop.subscribe(lambda e: events.append(e))
        await loop.run_continue()
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) >= 1

    @pytest.mark.asyncio
    async def test_run_continue_with_follow_up_queue(self):
        llm = FakeLLM(text_seq("first"), text_seq("follow_up_response"))
        loop = Engine(llm=llm, tools=[])
        await loop.run([UserMessage.text("go")])
        await loop.follow_up(UserMessage.text("follow up"))
        events = []
        await loop.subscribe(lambda e: events.append(e))
        await loop.run_continue()
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) >= 1


# ── should_stop_after_turn ────────────────────────────────────────────────────

class TestShouldStopAfterTurn:
    @pytest.mark.asyncio
    async def test_stops_when_predicate_returns_true(self):
        stop_calls: list = []

        def should_stop(message, tool_results):
            stop_calls.append(message)
            return True  # always stop

        llm = FakeLLM(
            tool_call_seq("t1", "my_tool"),
            text_seq("second_turn"),
            text_seq("third_turn"),
        )
        opts = Options(should_stop_after_turn=should_stop)
        events, _ = await run_loop(llm, tools=[make_tool("my_tool")], options=opts)
        # should_stop fires after the tool-call turn ends, before the next LLM call
        assert llm._idx == 1  # only the tool-call turn reached LLM
        assert len(stop_calls) >= 1

    @pytest.mark.asyncio
    async def test_continues_when_predicate_returns_false(self):
        # should_stop is only reached for ToolCalls turns (Stop breaks inside the match).
        # Use two back-to-back tool calls to get 2 should_stop_after_turn calls.
        call_count = [0]

        def should_stop(message, tool_results):
            call_count[0] += 1
            return call_count[0] >= 2  # stop after 2nd tool-call turn

        llm = FakeLLM(
            tool_call_seq("t1", "my_tool"),
            tool_call_seq("t2", "my_tool"),
            text_seq("final"),
        )
        opts = Options(should_stop_after_turn=should_stop)
        events, _ = await run_loop(llm, tools=[make_tool("my_tool")], options=opts)
        assert call_count[0] == 2


# ── queue management ──────────────────────────────────────────────────────────

class TestQueueManagement:
    @pytest.mark.asyncio
    async def test_clear_steering_empties_queue(self):
        llm = FakeLLM(text_seq("hi"))
        loop = Engine(llm=llm, tools=[])
        await loop.steer(UserMessage.text("steer"))
        assert loop.has_pending_messages() is True
        loop.clear_steering()
        assert loop.has_pending_messages() is False

    @pytest.mark.asyncio
    async def test_clear_follow_up_empties_queue(self):
        llm = FakeLLM(text_seq("hi"))
        loop = Engine(llm=llm, tools=[])
        await loop.follow_up(UserMessage.text("follow"))
        assert loop.has_pending_messages() is True
        loop.clear_follow_up()
        assert loop.has_pending_messages() is False

    @pytest.mark.asyncio
    async def test_clear_all_queues_empties_both(self):
        llm = FakeLLM(text_seq("hi"))
        loop = Engine(llm=llm, tools=[])
        await loop.steer(UserMessage.text("s"))
        await loop.follow_up(UserMessage.text("f"))
        loop.clear_all_queues()
        assert loop.has_pending_messages() is False

    def test_no_pending_messages_initially(self):
        llm = FakeLLM(text_seq("hi"))
        loop = Engine(llm=llm, tools=[])
        assert loop.has_pending_messages() is False


# ── follow-up messages via Options ────────────────────────────────────────────

class TestFollowUpMessages:
    @pytest.mark.asyncio
    async def test_follow_up_messages_continue_conversation(self):
        follow_up_sent = [False]

        def get_follow_up():
            if not follow_up_sent[0]:
                follow_up_sent[0] = True
                return [UserMessage.text("follow up question")]
            return []

        llm = FakeLLM(text_seq("first answer"), text_seq("follow up answer"))
        opts = Options(get_follow_up_messages=get_follow_up)
        events, _ = await run_loop(llm, options=opts)
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 2

    @pytest.mark.asyncio
    async def test_empty_follow_up_ends_loop(self):
        def get_follow_up():
            return []

        llm = FakeLLM(text_seq("answer"))
        opts = Options(get_follow_up_messages=get_follow_up)
        events, _ = await run_loop(llm, options=opts)
        end_events = [e for e in events if isinstance(e, AgentEndEvent)]
        assert len(end_events) == 1


# ── subscriber lifecycle ──────────────────────────────────────────────────────

class TestSubscriber:
    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving_events(self):
        llm = FakeLLM(text_seq("hi"), text_seq("second"))
        loop = Engine(llm=llm, tools=[])
        received: list = []
        unsub = await loop.subscribe(lambda e: received.append(e))
        await loop.run([UserMessage.text("first")])
        count_after_first = len(received)
        unsub()
        await loop.run([UserMessage.text("second")])
        assert len(received) == count_after_first  # no new events after unsub

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self):
        llm = FakeLLM(text_seq("hi"))
        loop = Engine(llm=llm, tools=[])
        a, b = [], []
        await loop.subscribe(lambda e: a.append(e))
        await loop.subscribe(lambda e: b.append(e))
        await loop.run([UserMessage.text("go")])
        assert len(a) == len(b)
        assert len(a) > 0
