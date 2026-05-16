"""Tests for Engine: event ordering, state transitions, tool calls, errors, abort."""
import asyncio
import pytest
from typing import AsyncIterator
from pydantic import BaseModel

from program.engine.engine import Engine
from program.engine.types import (
    AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent,
    Options, AgentEvent,
)
from program.inference.types import (
    LLMContext, LLMEvent,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
    StopReason,
)
from program.message.types import (
    UserMessage, AssistantMessage, TextContent, ToolCallContent, ToolResultContent, Role,
)
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    """Replays pre-configured event sequences, one sequence per stream() call."""
    def __init__(self, *sequences: list[LLMEvent]):
        self._sequences = list(sequences)
        self._call_index = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._sequences[self._call_index]
        self._call_index += 1
        for event in events:
            yield event


def text_sequence(text: str) -> list[LLMEvent]:
    """Minimal streaming events for a plain text response."""
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def tool_call_sequence(tool_id: str, tool_name: str, args: dict) -> list[LLMEvent]:
    """Minimal streaming events for a response that makes one tool call."""
    import json
    tc = ToolCallContent(id=tool_id, name=tool_name, args=args)
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=tool_name)),
        ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def error_sequence(error_msg: str = "boom") -> list[LLMEvent]:
    """Streaming events that end with an error."""
    return [
        StartEvent(),
        ErrorEvent(reason=StopReason.Error, error=error_msg),
    ]


# ── Fake Tool ─────────────────────────────────────────────────────────────────

class AnyParams(BaseModel):
    pass

def make_tool(name: str, result: str = "ok", is_error: bool = False) -> Tool:
    class FakeTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs) -> ToolResult:
            if is_error:
                return ToolResult.error(invocation.id, result)
            return ToolResult.ok(invocation.id, result)
    return FakeTool(name=name, description="fake", schema=AnyParams, kind=ToolKind.Read)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def run_loop(llm, tools=None, messages=None, options=None) -> tuple[list, Engine]:
    """Run the loop and return (collected_events, loop)."""
    loop = Engine(llm=llm, tools=tools or [], options=options or Options())
    events: list[AgentEvent] = []
    await loop.subscribe(lambda e: events.append(e))
    msgs = messages or [UserMessage.text("hello")]
    await loop.run(msgs)
    return events, loop


def event_types(events) -> list[str]:
    return [type(e).__name__ for e in events]


# ── Basic text response ───────────────────────────────────────────────────────

class TestTextResponse:
    @pytest.mark.asyncio
    async def test_event_sequence(self):
        llm = FakeLLM(text_sequence("Hello!"))
        events, _ = await run_loop(llm)
        types = event_types(events)
        assert types[0] == "AgentStartEvent"
        assert "MessageStartEvent" in types
        assert "MessageUpdateEvent" in types
        assert "MessageEndEvent" in types
        assert "TurnEndEvent" in types
        assert types[-1] == "AgentEndEvent"

    @pytest.mark.asyncio
    async def test_message_content_assembled(self):
        llm = FakeLLM(text_sequence("Hi there"))
        events, loop = await run_loop(llm)
        end_events = [e for e in events if isinstance(e, MessageEndEvent)]
        assistant_msgs = [e.message for e in end_events if e.message and e.message.role == Role.ASSISTANT]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].text_content() == "Hi there"

    @pytest.mark.asyncio
    async def test_stop_reason_stop(self):
        llm = FakeLLM(text_sequence("done"))
        _, loop = await run_loop(llm)
        assert loop.state.error_message is None

    @pytest.mark.asyncio
    async def test_no_error_in_state(self):
        llm = FakeLLM(text_sequence("fine"))
        _, loop = await run_loop(llm)
        assert loop.state.error_message is None


# ── Error response ────────────────────────────────────────────────────────────

class TestErrorResponse:
    @pytest.mark.asyncio
    async def test_error_sets_state(self):
        llm = FakeLLM(error_sequence("network timeout"))
        _, loop = await run_loop(llm)
        assert loop.state.error_message == "network timeout"

    @pytest.mark.asyncio
    async def test_agent_error_event_emitted(self):
        llm = FakeLLM(error_sequence("oops"))
        events, _ = await run_loop(llm)
        error_events = [e for e in events if isinstance(e, AgentErrorEvent)]
        assert len(error_events) == 1
        assert "oops" in error_events[0].error

    @pytest.mark.asyncio
    async def test_loop_stops_after_error(self):
        """Engine must not continue after an error turn."""
        llm = FakeLLM(error_sequence("fail"), text_sequence("second"))
        events, _ = await run_loop(llm)
        turn_ends = [e for e in events if isinstance(e, TurnEndEvent)]
        assert len(turn_ends) == 1  # only one turn ran


# ── Tool call response ────────────────────────────────────────────────────────

class TestToolCallResponse:
    @pytest.mark.asyncio
    async def test_tool_is_executed(self):
        tool_events = tool_call_sequence("tc1", "my_tool", {})
        llm = FakeLLM(tool_events, text_sequence("done"))
        events, _ = await run_loop(llm, tools=[make_tool("my_tool", "result_value")])
        exec_starts = [e for e in events if isinstance(e, ToolExecutionStartEvent)]
        assert len(exec_starts) == 1
        assert exec_starts[0].tool_call.name == "my_tool"

    @pytest.mark.asyncio
    async def test_tool_result_sent_back(self):
        """After tool execution a second LLM call must happen."""
        tool_events = tool_call_sequence("tc1", "my_tool", {})
        llm = FakeLLM(tool_events, text_sequence("final answer"))
        events, _ = await run_loop(llm, tools=[make_tool("my_tool", "result_value")])
        # Two MessageEnd events: one for tool call turn, one for final answer turn
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 2

    @pytest.mark.asyncio
    async def test_tool_execution_end_event(self):
        tool_events = tool_call_sequence("tc1", "my_tool", {})
        llm = FakeLLM(tool_events, text_sequence("final"))
        events, _ = await run_loop(llm, tools=[make_tool("my_tool", "tool_output")])
        exec_ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        assert len(exec_ends) == 1
        assert exec_ends[0].tool_result.content == "tool_output"

    @pytest.mark.asyncio
    async def test_missing_tool_produces_error_result(self):
        """A call to an unregistered tool should yield an error tool result and still
        make a follow-up LLM call (loop does not crash)."""
        tool_events = tool_call_sequence("tc1", "ghost_tool", {})
        llm = FakeLLM(tool_events, text_sequence("done"))
        events, loop = await run_loop(llm, tools=[])
        # Missing-tool errors are returned directly without ToolExecutionStart/End events.
        # The loop should still make a second LLM call and finish cleanly.
        assert loop.state.error_message is None
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 2  # tool-call turn + follow-up text turn


# ── Abort signal ──────────────────────────────────────────────────────────────

class TestAbort:
    @pytest.mark.asyncio
    async def test_abort_during_tool_phase_stops_next_llm_call(self):
        """Aborting inside after_tool_call sets the signal; the loop checks it at
        the top of the next iteration and exits before making another LLM call."""
        tool_events = tool_call_sequence("tc1", "my_tool", {})
        # Only one sequence: if the loop incorrectly makes a second call it will IndexError
        llm = FakeLLM(tool_events)

        loop_ref: list[Engine] = []

        def after_tool(raw, signal):
            # Abort after the tool executes; the next iteration will see the signal
            if loop_ref:
                loop_ref[0].abort()
            return raw

        opts = Options(after_tool_call=after_tool)
        loop = Engine(llm=llm, tools=[make_tool("my_tool")], options=opts)
        loop_ref.append(loop)

        events: list = []
        await loop.subscribe(lambda e: events.append(e))
        await loop.run([UserMessage.text("go")])

        # The loop must have stopped — no second assistant MessageEnd
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 1  # only the tool-call turn, no follow-up LLM response


# ── Follow-up messages ────────────────────────────────────────────────────────

class TestFollowUpMessages:
    @pytest.mark.asyncio
    async def test_follow_up_triggers_second_call(self):
        """When follow-up messages are provided, the loop continues."""
        called = [0]

        def get_follow_ups():
            if called[0] == 0:
                called[0] += 1
                return [UserMessage.text("follow up")]
            return []

        llm = FakeLLM(text_sequence("first"), text_sequence("second"))
        opts = Options(get_follow_up_messages=get_follow_ups)
        events, _ = await run_loop(llm, options=opts)
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 2


# ── should_stop_after_turn ────────────────────────────────────────────────────

class TestShouldStopAfterTurn:
    @pytest.mark.asyncio
    async def test_stop_callback_halts_loop(self):
        llm = FakeLLM(text_sequence("turn1"), text_sequence("turn2"))
        opts = Options(should_stop_after_turn=lambda msg, results: True)
        events, _ = await run_loop(llm, options=opts)
        turn_ends = [e for e in events if isinstance(e, TurnEndEvent)]
        assert len(turn_ends) == 1


# ── State ─────────────────────────────────────────────────────────────────────

class TestAgentState:
    @pytest.mark.asyncio
    async def test_is_idle_after_run(self):
        llm = FakeLLM(text_sequence("hi"))
        loop = Engine(llm=llm, tools=[])
        await loop.run([UserMessage.text("hello")])
        assert loop.is_idle

    @pytest.mark.asyncio
    async def test_reset_clears_error(self):
        llm = FakeLLM(error_sequence("err"))
        loop = Engine(llm=llm, tools=[])
        await loop.run([UserMessage.text("go")])
        assert loop.state.error_message is not None
        loop.reset()
        assert loop.state.error_message is None

    @pytest.mark.asyncio
    async def test_messages_in_state(self):
        llm = FakeLLM(text_sequence("hello back"))
        loop = Engine(llm=llm, tools=[])
        msgs = [UserMessage.text("hello")]
        await loop.run(msgs)
        # State should record the assistant message
        roles = [m.role for m in loop.state.messages]
        assert Role.ASSISTANT in roles
