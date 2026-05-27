"""Tests that steer() and follow_up() are drained from the queue during _loop() execution."""
import asyncio
import pytest
from pydantic import BaseModel
from typing import AsyncIterator

from operator_use.engine.service import Engine
from operator_use.engine.types import Options, SteeringMode, FollowupMode
from operator_use.inference.types import (
    LLMContext, LLMEvent,
    StartEvent, EndEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
    StopReason,
)
from operator_use.message.types import (
    UserMessage, TextContent, ToolCallContent,
)
from operator_use.tool.types import Tool, ToolKind, ToolResult


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._sequences = list(sequences)
        self._call_index = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._sequences[self._call_index % len(self._sequences)]
        self._call_index += 1
        for event in events:
            yield event


def text_seq(text: str = "done") -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def tool_seq(tool_id: str, tool_name: str, args: dict) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=tool_name, args=args)
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=tool_name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


class AnyParams(BaseModel):
    model_config = {"extra": "allow"}


def make_noop_tool(name: str = "noop") -> Tool:
    class NoopTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **_) -> ToolResult:
            return ToolResult.ok(invocation.id, "ok")
    return NoopTool(name=name, description="noop", schema=AnyParams, kind=ToolKind.Read)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSteeringQueueDrainedInLoop:
    @pytest.mark.asyncio
    async def test_steer_message_injected_after_tool_call(self):
        """steer() messages pre-queued before run() are picked up after tool execution."""
        tool = make_noop_tool("noop")
        llm = FakeLLM(
            tool_seq("t1", "noop", {}),
            text_seq("final"),
        )
        loop = Engine(
            llm=llm,
            tools=[tool],
            options=Options(steering_mode=SteeringMode.OneAtATime),
        )

        # Enqueue the steering message before the loop starts
        await loop.steer(UserMessage(contents=[TextContent(content="steer-injection")]))

        injected_texts: list[str] = []
        original_stream = llm.stream

        async def capturing_stream(context: LLMContext) -> AsyncIterator[LLMEvent]:
            for msg in context.messages:
                for c in getattr(msg, "contents", []):
                    if hasattr(c, "content") and c.content == "steer-injection":
                        injected_texts.append(c.content)
            async for event in original_stream(context):
                yield event

        llm.stream = capturing_stream

        messages = [UserMessage(contents=[TextContent(content="go")])]
        await loop.run(messages)

        assert "steer-injection" in injected_texts, (
            "Steering message was not passed to LLM context after tool execution"
        )

    @pytest.mark.asyncio
    async def test_steering_queue_empty_after_drain(self):
        """After _loop() drains the steering queue, it is empty."""
        tool = make_noop_tool("noop")
        llm = FakeLLM(tool_seq("t1", "noop", {}), text_seq())
        loop = Engine(llm=llm, tools=[tool], options=Options())
        await loop.steer(UserMessage.text("steer"))
        assert not loop.state.steering_queue.is_empty()
        await loop.run([UserMessage.text("go")])
        assert loop.state.steering_queue.is_empty()


class TestFollowupQueueDrainedInLoop:
    @pytest.mark.asyncio
    async def test_follow_up_message_continues_loop(self):
        """Messages enqueued via follow_up() keep the loop running after a Stop."""
        llm = FakeLLM(
            text_seq("first stop"),
            text_seq("second stop"),
        )
        loop = Engine(
            llm=llm,
            tools=[],
            options=Options(followup_mode=FollowupMode.OneAtATime),
        )

        # Enqueue a follow-up before running so it's already in the queue
        await loop.follow_up(UserMessage(contents=[TextContent(content="follow-up")]))

        messages = [UserMessage(contents=[TextContent(content="start")])]
        await loop.run(messages)

        # LLM was called twice: once for initial, once for follow-up
        assert llm._call_index == 2

    @pytest.mark.asyncio
    async def test_empty_follow_up_queue_stops_loop(self):
        """Engine stops on StopReason.Stop when follow-up queue is empty and no callback."""
        llm = FakeLLM(text_seq("only turn"))
        loop = Engine(llm=llm, tools=[], options=Options())
        messages = [UserMessage(contents=[TextContent(content="hi")])]
        await loop.run(messages)
        assert llm._call_index == 1


class TestSteeringQueueAndCallbackCombined:
    @pytest.mark.asyncio
    async def test_queue_and_callback_both_contribute(self):
        """Both the steering queue and get_steering_messages callback are merged."""
        tool = make_noop_tool("noop")
        collected: list[str] = []

        llm = FakeLLM(
            tool_seq("t1", "noop", {}),
            text_seq("done"),
        )

        original_stream = llm.stream

        async def capturing_stream(context: LLMContext) -> AsyncIterator[LLMEvent]:
            for msg in context.messages:
                for c in getattr(msg, "contents", []):
                    if hasattr(c, "content") and c.content in ("from-queue", "from-callback"):
                        collected.append(c.content)
            async for ev in original_stream(context):
                yield ev

        llm.stream = capturing_stream

        def callback_steering() -> list:
            return [UserMessage(contents=[TextContent(content="from-callback")])]

        loop = Engine(
            llm=llm,
            tools=[tool],
            options=Options(
                get_steering_messages=callback_steering,
                steering_mode=SteeringMode.OneAtATime,
            ),
        )
        await loop.steer(UserMessage(contents=[TextContent(content="from-queue")]))

        messages = [UserMessage(contents=[TextContent(content="start")])]
        await loop.run(messages)

        assert "from-queue" in collected
        assert "from-callback" in collected
