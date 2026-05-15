"""Tests for ToolRegistry: registration, execution modes, hooks, validation."""
import asyncio
import pytest
from typing import Optional, Type
from pydantic import BaseModel
from program.tool.registry import ToolRegistry
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from program.message.types import ToolCallContent, ToolResultContent
from program.engine.types import Options


# ── Helpers ───────────────────────────────────────────────────────────────────

class EchoParams(BaseModel):
    message: str

class SlowParams(BaseModel):
    delay: float = 0.0

class EmptyParams(BaseModel):
    pass


def make_echo_tool(name="echo", mode=ToolExecutionMode.Sequential) -> Tool:
    class EchoTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None) -> ToolResult:
            return ToolResult.ok(invocation.id, invocation.params["message"])
    return EchoTool(name=name, description="echoes message", schema=EchoParams, kind=ToolKind.Read, execution_mode=mode)


def make_error_tool(name="bad_tool") -> Tool:
    class ErrorTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None) -> ToolResult:
            return ToolResult.error(invocation.id, "something went wrong")
    return ErrorTool(name=name, description="always errors", schema=EmptyParams, kind=ToolKind.Read)


def make_slow_tool(name="slow", mode=ToolExecutionMode.Parallel) -> Tool:
    class SlowTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None) -> ToolResult:
            delay = invocation.params.get("delay", 0)
            await asyncio.sleep(delay)
            return ToolResult.ok(invocation.id, f"slow:{delay}")
    return SlowTool(name=name, description="slow tool", schema=SlowParams, kind=ToolKind.Read, execution_mode=mode)


def tool_call(name: str, args: dict, id: str = "tc1") -> ToolCallContent:
    return ToolCallContent(id=id, name=name, args=args)


# ── Registration ──────────────────────────────────────────────────────────────

class TestRegistration:
    def test_register_and_get(self):
        reg = ToolRegistry([make_echo_tool()])
        assert reg.get("echo") is not None

    def test_get_missing(self):
        reg = ToolRegistry()
        assert reg.get("nope") is None

    def test_unregister(self):
        reg = ToolRegistry([make_echo_tool()])
        reg.unregister("echo")
        assert reg.get("echo") is None

    def test_list(self):
        reg = ToolRegistry([make_echo_tool("a"), make_echo_tool("b")])
        names = {t.name for t in reg.list()}
        assert names == {"a", "b"}


# ── Single execute ────────────────────────────────────────────────────────────

class TestExecute:
    @pytest.mark.asyncio
    async def test_success(self):
        reg = ToolRegistry([make_echo_tool()])
        result = await reg.execute(tool_call("echo", {"message": "hi"}))
        assert result.content == "hi"
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_missing_tool(self):
        reg = ToolRegistry()
        result = await reg.execute(tool_call("ghost", {}))
        assert result.is_error
        assert "not found" in result.content

    @pytest.mark.asyncio
    async def test_invalid_params(self):
        reg = ToolRegistry([make_echo_tool()])
        result = await reg.execute(tool_call("echo", {}))  # missing 'message'
        assert result.is_error
        assert "Invalid parameters" in result.content

    @pytest.mark.asyncio
    async def test_tool_returns_error(self):
        reg = ToolRegistry([make_error_tool()])
        result = await reg.execute(tool_call("bad_tool", {}, id="t1"))
        assert result.is_error
        assert "something went wrong" in result.content

    @pytest.mark.asyncio
    async def test_emits_start_and_end_events(self):
        events = []
        async def emit(event):
            events.append(type(event).__name__)

        reg = ToolRegistry([make_echo_tool()])
        await reg.execute(tool_call("echo", {"message": "x"}), emit=emit)
        assert "ToolExecutionStartEvent" in events
        assert "ToolExecutionEndEvent" in events


# ── Hooks ─────────────────────────────────────────────────────────────────────

class TestHooks:
    @pytest.mark.asyncio
    async def test_before_tool_call_modifies_params(self):
        reg = ToolRegistry([make_echo_tool()])

        def before(invocation, signal):
            invocation.params["message"] = "overridden"
            return invocation

        opts = Options(before_tool_call=before)
        result = await reg.execute(tool_call("echo", {"message": "original"}), options=opts)
        assert result.content == "overridden"

    @pytest.mark.asyncio
    async def test_after_tool_call_modifies_result(self):
        reg = ToolRegistry([make_echo_tool()])

        def after(raw, signal):
            return ToolResult.ok(raw.id, raw.content.upper())

        opts = Options(after_tool_call=after)
        result = await reg.execute(tool_call("echo", {"message": "hello"}), options=opts)
        assert result.content == "HELLO"

    @pytest.mark.asyncio
    async def test_should_skip_tool_calls(self):
        reg = ToolRegistry([make_echo_tool()])

        def skip(tc):
            return ToolResultContent(id=tc.id, content="skipped", is_error=False)

        opts = Options(should_skip_tool_calls=skip)
        result = await reg.execute(tool_call("echo", {"message": "hi"}), options=opts)
        assert result.content == "skipped"


# ── Sequential execute ────────────────────────────────────────────────────────

class TestSequentialExecute:
    @pytest.mark.asyncio
    async def test_order_preserved(self):
        reg = ToolRegistry([make_echo_tool()])
        calls = [
            tool_call("echo", {"message": "a"}, id="1"),
            tool_call("echo", {"message": "b"}, id="2"),
            tool_call("echo", {"message": "c"}, id="3"),
        ]
        results = await reg.sequential_execute(calls)
        assert [r.content for r in results] == ["a", "b", "c"]


# ── Parallel execute ──────────────────────────────────────────────────────────

class TestParallelExecute:
    @pytest.mark.asyncio
    async def test_all_executed(self):
        reg = ToolRegistry([make_echo_tool("e1"), make_echo_tool("e2")])
        calls = [
            tool_call("e1", {"message": "x"}, id="1"),
            tool_call("e2", {"message": "y"}, id="2"),
        ]
        results = await reg.parallel_execute(calls)
        contents = {r.content for r in results}
        assert contents == {"x", "y"}

    @pytest.mark.asyncio
    async def test_runs_concurrently(self):
        reg = ToolRegistry([make_slow_tool("s1"), make_slow_tool("s2")])
        calls = [
            tool_call("s1", {"delay": 0.05}, id="1"),
            tool_call("s2", {"delay": 0.05}, id="2"),
        ]
        import time
        start = time.monotonic()
        await reg.parallel_execute(calls)
        elapsed = time.monotonic() - start
        assert elapsed < 0.09  # both ran concurrently, not 0.1s sequentially


# ── Batch execute ─────────────────────────────────────────────────────────────

class TestBatchExecute:
    @pytest.mark.asyncio
    async def test_sequential_and_parallel_mixed(self):
        reg = ToolRegistry([
            make_echo_tool("seq_tool", mode=ToolExecutionMode.Sequential),
            make_echo_tool("par_tool", mode=ToolExecutionMode.Parallel),
        ])
        calls = [
            tool_call("seq_tool", {"message": "seq"}, id="1"),
            tool_call("par_tool", {"message": "par"}, id="2"),
        ]
        results = await reg.batch_execute(calls)
        contents = {r.content for r in results}
        assert contents == {"seq", "par"}

    @pytest.mark.asyncio
    async def test_missing_tool_returns_error_inline(self):
        reg = ToolRegistry()
        results = await reg.batch_execute([tool_call("ghost", {}, id="g1")])
        assert len(results) == 1
        assert results[0].is_error
        assert "not found" in results[0].content
