"""Engine tool execution — parallel/batch modes, before/after hooks, terminate, validation."""
from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel

from helpers import (
    FakeLLM, AnyParams, make_tool, text_seq, tool_call_seq,
    collect_events, error_seq,
)

from program.engine.service import Engine
from program.engine.types import (
    AgentErrorEvent, ToolExecutionEndEvent, MessageEndEvent, Options,
)
from program.inference.types import StartEvent, EndEvent, StopReason, ToolCallEndEvent
from program.message.types import UserMessage, ToolCallContent, Role
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult


class TestToolContext:
    @pytest.mark.asyncio
    async def test_tool_receives_engine_context(self):
        seen = {}

        class ContextTool(Tool):
            async def execute(self, invocation, context=None, **kwargs):
                seen["context"] = context
                return ToolResult.ok(invocation.id, "ok")

        llm = FakeLLM(tool_call_seq("t1", "ctx"), text_seq())
        tool = ContextTool(name="ctx", description="x", schema=AnyParams, kind=ToolKind.Read)
        engine = Engine(llm=llm, tools=[tool])

        await engine.run([UserMessage.text("go")])

        assert seen["context"] is engine.tool_context
        assert seen["context"].llm is llm
        assert seen["context"].engine is engine


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_parallel_tools_run_concurrently(self):
        starts, ends = [], []

        class TimedTool(Tool):
            async def execute(self, invocation, **kwargs):
                starts.append(time.monotonic())
                await asyncio.sleep(0.05)
                ends.append(time.monotonic())
                return ToolResult.ok(invocation.id, "ok")

        names = [f"t{i}" for i in range(6)]
        tools = [TimedTool(name=n, description="x", schema=AnyParams, kind=ToolKind.Read,
                           execution_mode=ToolExecutionMode.Parallel) for n in names]
        seq = [StartEvent()]
        for i, n in enumerate(names):
            seq.append(ToolCallEndEvent(tool_call=ToolCallContent(id=f"id{i}", name=n)))
        seq.append(EndEvent(reason=StopReason.ToolCalls))
        llm = FakeLLM(seq, text_seq())
        engine = Engine(llm=llm, tools=tools, options=Options(execution_mode=ToolExecutionMode.Parallel))
        await engine.run([UserMessage.text("go")])
        assert len(starts) == 6
        assert any(s < min(ends) for s in starts[1:])

    @pytest.mark.asyncio
    async def test_batch_mode_splits_parallel_and_sequential(self):
        order = []

        class TrackTool(Tool):
            async def execute(self, invocation, **kwargs):
                order.append(invocation.name)
                if self.execution_mode == ToolExecutionMode.Parallel:
                    await asyncio.sleep(0.02)
                return ToolResult.ok(invocation.id, "ok")

        par_a = TrackTool(name="par_a", description="x", schema=AnyParams, kind=ToolKind.Read, execution_mode=ToolExecutionMode.Parallel)
        par_b = TrackTool(name="par_b", description="x", schema=AnyParams, kind=ToolKind.Read, execution_mode=ToolExecutionMode.Parallel)
        seq_c = TrackTool(name="seq_c", description="x", schema=AnyParams, kind=ToolKind.Read)
        seq = [StartEvent(), ToolCallEndEvent(tool_call=ToolCallContent(id="1", name="par_a")),
               ToolCallEndEvent(tool_call=ToolCallContent(id="2", name="par_b")),
               ToolCallEndEvent(tool_call=ToolCallContent(id="3", name="seq_c")),
               EndEvent(reason=StopReason.ToolCalls)]
        llm = FakeLLM(seq, text_seq())
        engine = Engine(llm=llm, tools=[par_a, par_b, seq_c], options=Options(execution_mode=ToolExecutionMode.Batch))
        await engine.run([UserMessage.text("go")])
        assert order.index("seq_c") > max(order.index("par_a"), order.index("par_b"))

    @pytest.mark.asyncio
    async def test_unknown_tool_produces_error_result_in_tool_message(self):
        tc = ToolCallContent(id="t1", name="ghost", args={})
        seq = [StartEvent(), ToolCallEndEvent(tool_call=tc), EndEvent(reason=StopReason.ToolCalls)]
        engine = Engine(llm=FakeLLM(seq, text_seq()), tools=[])
        events = await collect_events(engine)
        tool_msg_events = [e for e in events if isinstance(e, MessageEndEvent) and
                           getattr(e.message, 'role', None) == Role.TOOL]
        assert tool_msg_events
        assert any(r.is_error for r in tool_msg_events[0].message.contents)


class TestBeforeAfterHooks:
    @pytest.mark.asyncio
    async def test_before_hook_cancels_execution(self):
        executed = []

        class Track(Tool):
            async def execute(self, invocation, **kwargs):
                executed.append(True)
                return ToolResult.ok(invocation.id, "no")

        from program.message.types import ToolResultContent

        async def before(inv, sig):
            return ToolResultContent(id=inv.id, content="blocked", is_error=False, metadata={})

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[Track(name="t", description="x", schema=AnyParams, kind=ToolKind.Read)],
                        options=Options(before_tool_call=before))
        events = await collect_events(engine)
        assert not executed
        ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        assert ends[0].tool_result.content == "blocked"

    @pytest.mark.asyncio
    async def test_before_hook_modifies_invocation_params(self):
        seen = []

        class Track(Tool):
            async def execute(self, invocation, **kwargs):
                seen.append(dict(invocation.params))
                return ToolResult.ok(invocation.id, "ok")

        async def before(inv, sig):
            return ToolInvocation(id=inv.id, name=inv.name, params={"injected": True})

        tc = ToolCallContent(id="t1", name="t", args={"orig": True})
        seq = [StartEvent(), ToolCallEndEvent(tool_call=tc), EndEvent(reason=StopReason.ToolCalls)]
        engine = Engine(llm=FakeLLM(seq, text_seq()),
                        tools=[Track(name="t", description="x", schema=AnyParams, kind=ToolKind.Read)],
                        options=Options(before_tool_call=before))
        await engine.run([UserMessage.text("go")])
        assert seen and seen[0].get("injected") is True

    @pytest.mark.asyncio
    async def test_after_hook_replaces_result(self):
        from program.tool.types import ToolResult as TR

        async def after(inv, result, sig):
            return TR(id=result.id, content="SWAPPED", is_error=False, metadata={})

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t", "orig")],
                        options=Options(after_tool_call=after))
        events = await collect_events(engine)
        ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        assert ends[0].tool_result.content == "SWAPPED"

    @pytest.mark.asyncio
    async def test_before_hook_exception_becomes_agent_error(self):
        async def before(inv, sig):
            raise RuntimeError("hook exploded")

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t")],
                        options=Options(before_tool_call=before))
        events = await collect_events(engine)
        assert any(isinstance(e, AgentErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_before_and_after_both_called_in_order(self):
        log = []
        async def before(inv, sig): log.append("before"); return inv
        async def after(inv, result, sig): log.append("after"); return result

        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()),
                        tools=[make_tool("t")],
                        options=Options(before_tool_call=before, after_tool_call=after))
        await engine.run([UserMessage.text("go")])
        assert log == ["before", "after"]


class TestToolTerminate:
    @pytest.mark.asyncio
    async def test_single_terminate_stops_loop(self):
        llm = FakeLLM(tool_call_seq("t1", "kill"), text_seq("NO"))
        engine = Engine(llm=llm, tools=[make_tool("kill", terminate=True)])
        await engine.run([UserMessage.text("go")])
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_all_terminate_stops_loop(self):
        seq = [StartEvent(), ToolCallEndEvent(tool_call=ToolCallContent(id="t1", name="ka")),
               ToolCallEndEvent(tool_call=ToolCallContent(id="t2", name="kb")),
               EndEvent(reason=StopReason.ToolCalls)]
        llm = FakeLLM(seq, text_seq("NO"))
        engine = Engine(llm=llm, tools=[make_tool("ka", terminate=True), make_tool("kb", terminate=True)])
        await engine.run([UserMessage.text("go")])
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_partial_terminate_continues(self):
        seq = [StartEvent(), ToolCallEndEvent(tool_call=ToolCallContent(id="t1", name="kill")),
               ToolCallEndEvent(tool_call=ToolCallContent(id="t2", name="keep")),
               EndEvent(reason=StopReason.ToolCalls)]
        llm = FakeLLM(seq, text_seq("continue"))
        engine = Engine(llm=llm, tools=[make_tool("kill", terminate=True), make_tool("keep")])
        await engine.run([UserMessage.text("go")])
        assert llm.call_count == 2


class TestToolValidation:
    @pytest.mark.asyncio
    async def test_valid_params_execute_successfully(self):
        class Strict(BaseModel):
            name: str
            count: int

        class StrictTool(Tool):
            async def execute(self, invocation, **kwargs):
                return ToolResult.ok(invocation.id, f"got {invocation.params['name']}")

        tool = StrictTool(name="s", description="x", schema=Strict, kind=ToolKind.Read)
        tc = ToolCallContent(id="t1", name="s", args={"name": "alice", "count": 3})
        seq = [StartEvent(), ToolCallEndEvent(tool_call=tc), EndEvent(reason=StopReason.ToolCalls)]
        engine = Engine(llm=FakeLLM(seq, text_seq()), tools=[tool])
        events = await collect_events(engine)
        ends = [e for e in events if isinstance(e, ToolExecutionEndEvent)]
        assert ends and not ends[0].tool_result.is_error

    @pytest.mark.asyncio
    async def test_invalid_params_produce_error_in_tool_message(self):
        class Strict(BaseModel):
            name: str

        class StrictTool(Tool):
            async def execute(self, invocation, **kwargs):
                return ToolResult.ok(invocation.id, "ok")

        tool = StrictTool(name="s", description="x", schema=Strict, kind=ToolKind.Read)
        tc = ToolCallContent(id="t1", name="s", args={})  # missing name
        seq = [StartEvent(), ToolCallEndEvent(tool_call=tc), EndEvent(reason=StopReason.ToolCalls)]
        engine = Engine(llm=FakeLLM(seq, text_seq()), tools=[tool])
        events = await collect_events(engine)
        tool_msg = [e for e in events if isinstance(e, MessageEndEvent) and
                    getattr(e.message, 'role', None) == Role.TOOL]
        assert tool_msg and any(r.is_error for r in tool_msg[0].message.contents)
