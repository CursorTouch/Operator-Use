"""Advanced Engine tests: steering, multi-tool, thinking content, error recovery."""
import pytest
from typing import AsyncIterator
from pydantic import BaseModel

from operator_use.engine.service import Engine
from operator_use.engine.types import (
    AgentEndEvent, AgentErrorEvent, AgentStartEvent,
    MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent,
    TurnEndEvent, Options, AgentEvent,
)
from operator_use.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from operator_use.message.types import (
    UserMessage, AssistantMessage, TextContent, ThinkingContent,
    ToolCallContent, ToolResultContent, Role,
)
from operator_use.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult


# ── Reusable helpers (same pattern as test_agent_loop.py) ─────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._sequences = list(sequences)
        self._call_index = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._sequences[self._call_index]
        self._call_index += 1
        for event in events:
            yield event


class AnyParams(BaseModel):
    pass

def make_tool(name: str, result: str = "tool_ok") -> Tool:
    class FakeTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs) -> ToolResult:
            return ToolResult.ok(invocation.id, result)
    return FakeTool(name=name, description="fake", schema=AnyParams, kind=ToolKind.Read)


def text_seq(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def thinking_then_text_seq(thinking: str, text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        ThinkingStartEvent(thinking=ThinkingContent(content="")),
        ThinkingDeltaEvent(thinking=ThinkingContent(content=thinking)),
        ThinkingEndEvent(thinking=ThinkingContent(content=thinking)),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def tool_call_seq(tool_id: str, name: str, args: dict = {}) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args=args)
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


async def run_loop(llm, tools=None, options=None, messages=None):
    loop = Engine(llm=llm, tools=tools or [], options=options or Options())
    events: list[AgentEvent] = []
    await loop.subscribe(lambda e: events.append(e))
    await loop.run(messages or [UserMessage.text("go")])
    return events, loop


# ── Thinking content ──────────────────────────────────────────────────────────

class TestThinkingContent:
    @pytest.mark.asyncio
    async def test_thinking_in_message(self):
        llm = FakeLLM(thinking_then_text_seq("let me think", "here is the answer"))
        events, _ = await run_loop(llm)
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 1
        msg = msg_ends[0].message
        assert isinstance(msg, AssistantMessage)
        assert msg.thinking()  # thinking content present
        assert msg.text_content() == "here is the answer"

    @pytest.mark.asyncio
    async def test_thinking_update_events_emitted(self):
        llm = FakeLLM(thinking_then_text_seq("hmm", "ok"))
        events, _ = await run_loop(llm)
        updates = [e for e in events if isinstance(e, MessageUpdateEvent)
                   and e.message and e.message.role == Role.ASSISTANT]
        thinking_updates = [
            e for e in updates
            if any(getattr(c, 'type', '') == 'thinking' for c in e.message.contents)
        ]
        assert len(thinking_updates) >= 1


# ── Multiple tool calls in one turn ──────────────────────────────────────────

class TestMultipleToolCalls:
    @pytest.mark.asyncio
    async def test_two_tools_both_executed(self):
        tc1 = ToolCallContent(id="t1", name="tool_a", args={})
        tc2 = ToolCallContent(id="t2", name="tool_b", args={})
        two_tools_seq = [
            StartEvent(),
            ToolCallStartEvent(tool_call=ToolCallContent(id="t1", name="tool_a")),
            ToolCallEndEvent(tool_call=tc1),
            ToolCallStartEvent(tool_call=ToolCallContent(id="t2", name="tool_b")),
            ToolCallEndEvent(tool_call=tc2),
            EndEvent(reason=StopReason.ToolCalls),
        ]
        llm = FakeLLM(two_tools_seq, text_seq("done"))
        events, _ = await run_loop(llm, tools=[make_tool("tool_a"), make_tool("tool_b")])
        exec_starts = [e for e in events if isinstance(e, ToolExecutionStartEvent)]
        assert len(exec_starts) == 2
        names = {e.tool_call.name for e in exec_starts}
        assert names == {"tool_a", "tool_b"}

    @pytest.mark.asyncio
    async def test_sequential_tool_calls_across_turns(self):
        """Two separate LLM turns each making one tool call, followed by a text response."""
        llm = FakeLLM(
            tool_call_seq("t1", "first_tool"),
            tool_call_seq("t2", "second_tool"),
            text_seq("final"),
        )
        events, _ = await run_loop(llm, tools=[make_tool("first_tool"), make_tool("second_tool")])
        exec_ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        assert len(exec_ends) == 2

    @pytest.mark.asyncio
    async def test_tool_result_passed_to_next_llm_call(self):
        """After a tool call, the tool message is included in the next LLM context."""
        captured_contexts: list[LLMContext] = []

        class CapturingLLM:
            _seqs = [tool_call_seq("t1", "my_tool"), text_seq("done")]
            _idx = 0
            async def stream(self, context: LLMContext):
                captured_contexts.append(context)
                for e in self._seqs[self._idx]:
                    yield e
                self._idx += 1

        llm = CapturingLLM()
        loop = Engine(llm=llm, tools=[make_tool("my_tool", "the_result")])
        await loop.run([UserMessage.text("go")])

        # Second context should contain a ToolMessage with the result
        assert len(captured_contexts) == 2
        second_ctx_roles = [m.role for m in captured_contexts[1].messages]
        assert Role.TOOL in second_ctx_roles


# ── Steering messages ─────────────────────────────────────────────────────────

class TestSteeringMessages:
    @pytest.mark.asyncio
    async def test_steering_injected_after_tool_call(self):
        """get_steering_messages is called after tool execution and those messages
        are appended to the context for the next LLM call."""
        steering_injected = []

        def get_steering():
            msg = UserMessage.text("steering context")
            steering_injected.append(msg)
            return [msg] if len(steering_injected) == 1 else []

        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("steered response"))
        opts = Options(get_steering_messages=get_steering)
        events, _ = await run_loop(llm, tools=[make_tool("my_tool")], options=opts)
        assert len(steering_injected) >= 1
        # Engine should complete with a final text response
        msg_ends = [e for e in events if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 2  # tool-call turn + steered text turn


# ── Consecutive errors then success (simulating retry externally) ─────────────

class TestLoopReset:
    @pytest.mark.asyncio
    async def test_reset_then_rerun_succeeds(self):
        """After reset(), the loop can be run again from a clean state."""
        llm = FakeLLM(
            [StartEvent(), ErrorEvent(reason=StopReason.Error, error="first attempt fails")],
            text_seq("second attempt ok"),
        )
        loop = Engine(llm=llm, tools=[])
        events1: list = []
        await loop.subscribe(lambda e: events1.append(e))
        await loop.run([UserMessage.text("go")])
        assert loop.state.error_message is not None

        loop.reset()
        assert loop.state.error_message is None

        events2: list = []
        await loop.subscribe(lambda e: events2.append(e))
        await loop.run([UserMessage.text("go")])
        assert loop.state.error_message is None
        msg_ends = [e for e in events2 if isinstance(e, MessageEndEvent)
                    and e.message and e.message.role == Role.ASSISTANT]
        assert len(msg_ends) == 1


# ── transform_context ─────────────────────────────────────────────────────────

class TestTransformContext:
    @pytest.mark.asyncio
    async def test_transform_can_modify_messages(self):
        captured = []

        def transform(messages, signal):
            captured.extend(messages)
            return messages

        llm = FakeLLM(text_seq("hi"))
        opts = Options(transform_context=transform)
        await run_loop(llm, options=opts, messages=[UserMessage.text("original")])
        assert any(
            m.role == Role.USER for m in captured
        )

    @pytest.mark.asyncio
    async def test_transform_can_filter_messages(self):
        """Transform that removes all but the last message."""
        class CapturingLLM:
            captured = []
            async def stream(self, context: LLMContext):
                self.captured.extend(context.messages)
                for e in text_seq("ok"):
                    yield e

        llm = CapturingLLM()
        msgs = [UserMessage.text("old1"), UserMessage.text("old2"), UserMessage.text("keep")]

        def transform(messages, signal):
            return [messages[-1]]  # only keep last

        loop = Engine(llm=llm, tools=[], options=Options(transform_context=transform))
        await loop.run(msgs)
        assert len(llm.captured) == 1
        assert llm.captured[0].contents[0].content == "keep"


# ── Pending tool calls tracking ───────────────────────────────────────────────

class TestPendingToolCalls:
    @pytest.mark.asyncio
    async def test_pending_cleared_after_tool_completes(self):
        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("done"))
        loop = Engine(llm=llm, tools=[make_tool("my_tool")])
        await loop.run([UserMessage.text("go")])
        assert len(loop.state.pending_tool_calls) == 0

    @pytest.mark.asyncio
    async def test_pending_tracked_during_execution(self):
        in_flight: list[set] = []

        class TrackingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                return ToolResult.ok(invocation.id, "ok")

        real_tool = TrackingTool(name="tracker", description="x", schema=AnyParams, kind=ToolKind.Read)

        original_execute = real_tool.execute

        async def patched_execute(invocation, **kwargs):
            return await original_execute(invocation, **kwargs)

        llm = FakeLLM(tool_call_seq("t1", "tracker"), text_seq("done"))
        loop = Engine(llm=llm, tools=[real_tool])

        events = []
        async def capture(event):
            if isinstance(event, ToolExecutionStartEvent):
                in_flight.append(set(loop.state.pending_tool_calls))
            events.append(event)

        await loop.subscribe(capture)
        await loop.run([UserMessage.text("go")])
        # At ToolExecutionStartEvent, pending_tool_calls should contain the ID
        assert any("t1" in s for s in in_flight)
