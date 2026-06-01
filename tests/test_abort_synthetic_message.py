"""Tests for synthetic '[Operation interrupted by user]' messages on abort.

Three scenarios:
1. Abort BEFORE the LLM call (signal set before streaming starts).
2. Abort DURING LLM streaming (provider emits StopReason.Abort).
3. Abort AFTER tool execution completes (signal set post-tool, pre-next-LLM).
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel
from types import SimpleNamespace

from operator_use.agent.types import AgentContext
from operator_use.engine.service import Engine
from operator_use.engine.types import (
    AgentErrorEvent, MessageEndEvent, Options,
)
from operator_use.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from operator_use.message.types import (
    AssistantMessage, TextContent, ToolCallContent, UserMessage, Role,
)
from operator_use.tool.types import Tool, ToolKind, ToolInvocation, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class AnyParams(BaseModel):
    pass


class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0
        self.call_count = 0
        self.model = SimpleNamespace(name="fake", provider="fake")
        self.api = SimpleNamespace(options=SimpleNamespace())

    async def stream(self, context: LLMContext):
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        self.call_count += 1
        for e in events:
            yield e


def text_seq(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def abort_seq() -> list[LLMEvent]:
    """Provider-side abort — what streaming APIs emit on cancellation."""
    return [StartEvent(), ErrorEvent(reason=StopReason.Abort, error="Cancelled")]


def tool_seq(tool_id: str, name: str) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args={})
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def synthetic_message_end_events(events: list) -> list[MessageEndEvent]:
    """Return MessageEndEvents whose message contains the interruption text."""
    return [
        e for e in events
        if isinstance(e, MessageEndEvent)
        and e.message is not None
        and e.message.role == Role.ASSISTANT
        and any(
            isinstance(c, TextContent) and "[Operation interrupted by user]" in c.content
            for c in getattr(e.message, "contents", [])
        )
    ]


async def collect(engine: Engine, messages=None) -> list:
    events = []
    await engine.subscribe(lambda e: events.append(e))
    await engine.run(AgentContext(
        system_prompt="",
        messages=messages or [UserMessage.text("go")],
        tools=engine.tools,
    ))
    return events


# ---------------------------------------------------------------------------
# Case 1: abort BEFORE LLM call
# ---------------------------------------------------------------------------

class TestAbortBeforeLLMCall:
    @pytest.mark.asyncio
    async def test_synthetic_message_emitted(self):
        """Aborting via transform_context (fires before the signal check) emits
        the synthetic closing message, not a raw partial assistant."""
        engine_ref: list[Engine] = []

        def abort_in_transform(messages, signal):
            engine_ref[0].abort()
            return messages

        llm = FakeLLM(text_seq("should not reach"))
        engine = Engine(llm=llm, tools=[], options=Options(transform_context=abort_in_transform))
        engine_ref.append(engine)

        events = await collect(engine)

        synth = synthetic_message_end_events(events)
        assert len(synth) == 1, "Exactly one synthetic interruption message expected"

    @pytest.mark.asyncio
    async def test_no_agent_error_event(self):
        """Abort before LLM must not raise AgentErrorEvent — no retry should occur."""
        engine_ref: list[Engine] = []

        def abort_in_transform(messages, signal):
            engine_ref[0].abort()
            return messages

        llm = FakeLLM(text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[], options=Options(transform_context=abort_in_transform))
        engine_ref.append(engine)

        events = await collect(engine)

        assert not any(isinstance(e, AgentErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_llm_never_called(self):
        """LLM must not be invoked when abort fires before the call."""
        engine_ref: list[Engine] = []

        def abort_in_transform(messages, signal):
            engine_ref[0].abort()
            return messages

        llm = FakeLLM(text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[], options=Options(transform_context=abort_in_transform))
        engine_ref.append(engine)

        await collect(engine)

        assert llm.call_count == 0

    @pytest.mark.asyncio
    async def test_synthetic_message_has_stop_reason_stop(self):
        """The synthetic message must have stop_reason=Stop so it stays in LLM context."""
        engine_ref: list[Engine] = []

        def abort_in_transform(messages, signal):
            engine_ref[0].abort()
            return messages

        llm = FakeLLM(text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[], options=Options(transform_context=abort_in_transform))
        engine_ref.append(engine)

        events = await collect(engine)

        synth = synthetic_message_end_events(events)
        assert synth[0].message.stop_reason == StopReason.Stop


# ---------------------------------------------------------------------------
# Case 2: abort DURING LLM streaming
# ---------------------------------------------------------------------------

class TestAbortDuringStreaming:
    @pytest.mark.asyncio
    async def test_synthetic_message_emitted(self):
        """Provider-side abort emits the synthetic closing message."""
        llm = FakeLLM(abort_seq())
        engine = Engine(llm=llm, tools=[])

        events = await collect(engine)

        synth = synthetic_message_end_events(events)
        assert len(synth) == 1

    @pytest.mark.asyncio
    async def test_no_agent_error_event(self):
        """Abort mid-stream must not emit AgentErrorEvent."""
        llm = FakeLLM(abort_seq())
        engine = Engine(llm=llm, tools=[])

        events = await collect(engine)

        assert not any(isinstance(e, AgentErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_engine_error_message_is_none(self):
        """engine.state.error_message must be None after abort — no retry triggered."""
        llm = FakeLLM(abort_seq())
        engine = Engine(llm=llm, tools=[])

        await collect(engine)

        assert engine.state.error_message is None

    @pytest.mark.asyncio
    async def test_synthetic_message_has_stop_reason_stop(self):
        llm = FakeLLM(abort_seq())
        engine = Engine(llm=llm, tools=[])

        events = await collect(engine)

        synth = synthetic_message_end_events(events)
        assert synth[0].message.stop_reason == StopReason.Stop

    @pytest.mark.asyncio
    async def test_session_contains_synthetic_message(self):
        """Agent layer: session must contain the synthetic message after streaming abort."""
        from helpers import make_agent
        from operator_use.session.types import MessageEntry

        agent, sm = make_agent(FakeLLM(abort_seq()))
        await agent.invoke("hello")

        interrupt_entries = [
            e for e in sm.entries
            if isinstance(e, MessageEntry)
            and isinstance(e.message, AssistantMessage)
            and any(
                isinstance(c, TextContent) and "[Operation interrupted by user]" in c.content
                for c in getattr(e.message, "contents", [])
            )
        ]
        assert len(interrupt_entries) == 1


# ---------------------------------------------------------------------------
# Case 3: abort AFTER tool execution
# ---------------------------------------------------------------------------

class TestAbortAfterToolExecution:
    @pytest.mark.asyncio
    async def test_synthetic_message_emitted(self):
        """Aborting inside a tool execution triggers the synthetic closing message."""
        engine_ref: list[Engine] = []

        class AbortingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                engine_ref[0].abort()
                return ToolResult.ok(invocation.id, "done")

        tool = AbortingTool(name="aborter", description="t", schema=AnyParams, kind=ToolKind.Read)
        llm = FakeLLM(tool_seq("t1", "aborter"), text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[tool])
        engine_ref.append(engine)

        events = await collect(engine)

        synth = synthetic_message_end_events(events)
        assert len(synth) == 1

    @pytest.mark.asyncio
    async def test_no_agent_error_event(self):
        engine_ref: list[Engine] = []

        class AbortingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                engine_ref[0].abort()
                return ToolResult.ok(invocation.id, "done")

        tool = AbortingTool(name="aborter", description="t", schema=AnyParams, kind=ToolKind.Read)
        llm = FakeLLM(tool_seq("t1", "aborter"), text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[tool])
        engine_ref.append(engine)

        events = await collect(engine)

        assert not any(isinstance(e, AgentErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_second_llm_call_never_made(self):
        """After tool abort the engine must not make a second LLM call."""
        engine_ref: list[Engine] = []

        class AbortingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                engine_ref[0].abort()
                return ToolResult.ok(invocation.id, "done")

        tool = AbortingTool(name="aborter", description="t", schema=AnyParams, kind=ToolKind.Read)
        llm = FakeLLM(tool_seq("t1", "aborter"), text_seq("unreachable"))
        engine = Engine(llm=llm, tools=[tool])
        engine_ref.append(engine)

        await collect(engine)

        assert llm.call_count == 1  # only the tool-call turn, no second LLM call

    @pytest.mark.asyncio
    async def test_session_contains_tool_results_and_synthetic_message(self):
        """Session must include tool results AND the synthetic closing message."""
        from helpers import make_agent
        from operator_use.session.types import MessageEntry
        from operator_use.message.types import ToolMessage

        agent_ref: list = []

        class AbortingTool(Tool):
            async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
                agent_ref[0]._engine.abort()
                return ToolResult.ok(invocation.id, "tool output")

        tool = AbortingTool(name="aborter", description="t", schema=AnyParams, kind=ToolKind.Read)
        agent, sm = make_agent(
            FakeLLM(tool_seq("t1", "aborter"), text_seq("unreachable")),
            tools=[tool],
        )
        agent_ref.append(agent)
        await agent.invoke("hello")

        messages = [e.message for e in sm.entries if isinstance(e, MessageEntry)]

        has_tool_message = any(isinstance(m, ToolMessage) for m in messages)
        has_synthetic = any(
            isinstance(m, AssistantMessage)
            and any(
                isinstance(c, TextContent) and "[Operation interrupted by user]" in c.content
                for c in getattr(m, "contents", [])
            )
            for m in messages
        )

        assert has_tool_message, "Tool result message must be in session"
        assert has_synthetic, "Synthetic closing message must be in session"
