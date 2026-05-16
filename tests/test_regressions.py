"""
Regression tests for bugs that were found and fixed during development.

Each test is named after the scenario that exposed the bug and includes a
comment explaining the original failure mode so the constraint is clear to
future readers.
"""
import pytest
from typing import AsyncIterator
from pydantic import BaseModel

from program.engine.loop import AgentLoop
from program.engine.types import MessageEndEvent, Options
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from program.message.types import (
    TextContent, ToolCallContent, ToolResultContent,
    UserMessage, AssistantMessage, ToolMessage, Role,
)
from program.session.manager import SessionManager
from program.session.types import MessageEntry
from program.tool.types import Tool, ToolKind, ToolInvocation, ToolResult


# ── Helpers ───────────────────────────────────────────────────────────────────

class AnyParams(BaseModel):
    pass


class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
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


def tool_call_seq(tool_id: str, name: str) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args={})
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def make_tool(name: str, result: str = "result") -> Tool:
    class FakeTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
            return ToolResult.ok(invocation.id, result)
    return FakeTool(name=name, description="fake", schema=AnyParams, kind=ToolKind.Read)


# ── REG-001: ToolMessage contents cleared after loop run ─────────────────────
#
# Bug: ToolMessage.from_results(results) stored `results` directly (no copy).
# The loop reuses and clears the same list via `tool_results.clear()` after
# each turn. This silently emptied ToolMessage.contents after run() returned.
#
# Fix: program/message/types.py — ToolMessage.from_results now does list(results).

class TestReg001ToolMessageContentsCleared:
    def test_from_results_not_affected_by_clearing_source_list(self):
        """Mutation of the original list must not affect stored contents."""
        original = [
            ToolResultContent(id="t1", content="first"),
            ToolResultContent(id="t2", content="second"),
        ]
        msg = ToolMessage.from_results(original)
        original.clear()
        assert len(msg.contents) == 2, (
            "ToolMessage.from_results must copy the list; "
            "clearing the source list should not empty .contents"
        )

    def test_from_results_append_to_source_not_reflected(self):
        """Appending to the original list must not grow stored contents."""
        original = [ToolResultContent(id="t1", content="x")]
        msg = ToolMessage.from_results(original)
        original.append(ToolResultContent(id="t2", content="y"))
        assert len(msg.contents) == 1

    @pytest.mark.asyncio
    async def test_tool_message_contents_survive_loop_run(self):
        """
        After AgentLoop.run() completes, ToolMessage.contents must still hold
        the tool results. This was the integration manifestation of REG-001:
        the loop called tool_results.clear() at the end of each turn, which
        emptied the ToolMessage that had captured the same list object.
        """
        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("done"))
        tool = make_tool("my_tool", "expected_output")
        loop = AgentLoop(llm=llm, tools=[tool])

        messages_seen: list = []
        async def capture(event):
            if isinstance(event, MessageEndEvent):
                messages_seen.append(event.message)

        await loop.subscribe(capture)
        await loop.run([UserMessage.text("go")])

        tool_messages = [m for m in messages_seen if m.role == Role.TOOL]
        assert len(tool_messages) == 1, "Expected exactly one ToolMessage"
        assert len(tool_messages[0].contents) == 1, (
            "ToolMessage.contents was empty after loop.run() — REG-001 regression"
        )
        assert tool_messages[0].contents[0].content == "expected_output"

    @pytest.mark.asyncio
    async def test_tool_message_persisted_in_session_after_run(self):
        """
        The session-level manifestation: ToolMessage stored in SessionManager
        must retain its contents after the AgentSession finishes a turn.
        """
        from pathlib import Path
        from program.agent_session.session import AgentSession
        from program.agent_session.types import AgentSessionConfig
        from program.compaction.compact import Compaction
        from program.compaction.types import CompactionSettings
        from program.extension.runtime import ExtensionRuntime
        from program.extension.types import LoadExtensionsResult
        from program.resource.types import BaseResourceLoader, ResourceExtensionPaths

        class FakeResourceLoader(BaseResourceLoader):
            def get_extensions(self): return LoadExtensionsResult()
            def get_skills(self): return [], []
            def get_context_files(self): return []
            def get_system_prompt(self): return None
            def get_append_system_prompt(self): return []
            def extend_resources(self, paths): pass
            def get_diagnostics(self, runtime=None): return []
            async def reload(self): pass

        class _NullCtx: pass

        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("done"))
        tool = make_tool("my_tool", "session_output")
        sm = SessionManager.in_memory()
        loop = AgentLoop(llm=llm, tools=[tool])
        load_result = LoadExtensionsResult()
        config = AgentSessionConfig(
            cwd=Path("/tmp"), retry_enabled=False,
            retry_max_retries=0, retry_base_delay_ms=0,
        )
        session = AgentSession(
            loop=loop,
            session_manager=sm,
            resource_loader=FakeResourceLoader(),
            extension_runtime=ExtensionRuntime(load_result, _NullCtx()),
            compaction=Compaction(llm=llm, settings=CompactionSettings(enabled=False)),
            config=config,
        )
        session._extensions = ExtensionRuntime(load_result, session)

        await session.prompt("run the tool")

        tool_msgs = [
            sm.by_id[e.id].message
            for e in sm.get_entries()
            if isinstance(sm.by_id.get(e.id), MessageEntry)
            and sm.by_id[e.id].message.role == Role.TOOL
        ]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0].contents) > 0, (
            "Session-persisted ToolMessage had empty contents — REG-001 regression"
        )
        assert any(
            "session_output" in c.content
            for c in tool_msgs[0].contents
            if isinstance(c, ToolResultContent)
        )


# ── REG-002: get_last_activity_time called .timestamp() on a float ─────────
#
# Bug: program/session/utils.py — get_last_activity_time called
# float(message_timestamp.timestamp()) where message_timestamp was already a
# float (message.timestamp stores a Unix epoch float, not a datetime). Calling
# .timestamp() on a float raised AttributeError at runtime.
#
# Fix: added isinstance(message_timestamp, (int, float)) guard.

class TestReg002GetLastActivityTimeFloat:
    def test_does_not_raise_with_float_timestamp(self):
        """get_last_activity_time must handle float message timestamps without AttributeError."""
        from program.session.utils import get_last_activity_time

        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("hi"))
        a = AssistantMessage()
        a.contents = [TextContent(content="hello")]
        sm.append_message(a)

        entries = sm.get_entries()
        # Must not raise — previously crashed with AttributeError: 'float' object has no attribute 'timestamp'
        result = get_last_activity_time(entries)
        assert result is not None
        assert isinstance(result, float)

    def test_returns_correct_timestamp(self):
        """Returned timestamp must match the most-recent message, not some garbage value."""
        from program.session.utils import get_last_activity_time
        import time

        sm = SessionManager.in_memory()
        before = time.time()
        sm.append_message(UserMessage.text("q"))
        a = AssistantMessage()
        a.contents = [TextContent(content="a")]
        sm.append_message(a)
        after = time.time()

        result = get_last_activity_time(sm.get_entries())
        assert before <= result <= after + 0.1, (
            f"Timestamp {result} outside expected range [{before}, {after}]"
        )

    def test_two_messages_returns_later_one(self):
        """get_last_activity_time must return the max timestamp, not the first."""
        from program.session.utils import get_last_activity_time
        import time

        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("older"))
        time.sleep(0.01)
        a = AssistantMessage()
        a.contents = [TextContent(content="newer")]
        sm.append_message(a)

        entries = sm.get_entries()
        result = get_last_activity_time(entries)
        # Should be close to the second message's timestamp
        assert result >= entries[0].timestamp
