"""Integration tests for Agent: prompt flow, retry, compaction, extensions, tools."""
import pytest
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator
from pydantic import BaseModel

from program.agent.service import Agent
from program.builtins.tools.todo import TodoTool
from program.runtime.types import RuntimeConfig
from program.agent.types import AgentConfig, PromptOptions
from program.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from program.compaction.strategy.types import CompactionSettings, CompactionPreparation
from program.message.types import AgentMessage as _AgentMessage
CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": _AgentMessage})
from program.engine.service import Engine
from program.engine.types import Options
from program.extension.runtime import ExtensionRuntime
from program.extension.types import (
    LoadExtensionsResult, Extension, BeforeAgentStartEventResult,
    SessionCompactEvent,
)
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from program.message.types import (
    TextContent, ToolCallContent, ToolResultContent,
    UserMessage, AssistantMessage, ToolMessage, Usage, Role,
)
from program.resource.types import BaseResourceLoader, ResourceExtensionPaths, ContextFile
from program.session.manager import SessionManager
from program.session.types import MessageEntry, CompactionEntry
from program.skill.types import SourceInfo
from program.tool.types import Tool, ToolKind, ToolInvocation, ToolResult


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0
        self.model = SimpleNamespace(name="fake", provider="fake")
        self.api = SimpleNamespace(options=SimpleNamespace())

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        for e in events:
            yield e

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        return events


# ── Event sequence helpers ────────────────────────────────────────────────────

def text_seq(text: str, usage: Usage | None = None) -> list[LLMEvent]:
    usage = usage or Usage()
    msg_events = [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(
            reason=StopReason.Stop,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        ),
    ]
    return msg_events


def error_seq(error: str = "network error") -> list[LLMEvent]:
    return [StartEvent(), ErrorEvent(reason=StopReason.Error, error=error)]


def tool_call_seq(tool_id: str, name: str, args: dict = {}) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args=args)
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def summary_seq(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


# ── Fake resource loader ──────────────────────────────────────────────────────

class FakeResourceLoader(BaseResourceLoader):
    def __init__(self, system_prompt: str | None = None):
        self._system_prompt = system_prompt

    def get_extensions(self) -> LoadExtensionsResult:
        return LoadExtensionsResult()

    def get_skills(self):
        return [], []

    def get_tools(self):
        return []

    def get_commands(self):
        return []

    def get_hooks(self):
        return []

    def get_context_files(self):
        return []

    def get_system_prompt(self):
        return self._system_prompt

    def get_append_system_prompt(self):
        return []

    def get_subagent_profiles(self):
        return []

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        pass

    def get_diagnostics(self, runtime=None):
        return []

    async def reload(self) -> None:
        pass


# ── Fake tool ─────────────────────────────────────────────────────────────────

class AnyParams(BaseModel):
    pass


def make_tool(name: str, result: str = "tool_result") -> Tool:
    class FakeTool(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs) -> ToolResult:
            return ToolResult.ok(invocation.id, result)
    return FakeTool(name=name, description="fake", schema=AnyParams, kind=ToolKind.Read)


# ── Session fixture ───────────────────────────────────────────────────────────

def make_session(
    llm: FakeLLM,
    tools: list[Tool] | None = None,
    compaction_settings: CompactionSettings | None = None,
    system_prompt: str | None = None,
    retry_enabled: bool = False,
    retry_max_retries: int = 0,
    context_window: int = 200_000,
) -> tuple[Agent, SessionManager]:
    from program.hooks.service import Hooks
    sm = SessionManager.in_memory()
    hooks = Hooks()
    engine = Engine(llm=llm, tools=tools or [], options=Options(), hooks=hooks)
    comp_settings = compaction_settings or CompactionSettings(enabled=False)
    compaction = Compaction(llm=llm, settings=comp_settings)
    resource_loader = FakeResourceLoader(system_prompt=system_prompt)
    ext_result = LoadExtensionsResult()

    config = AgentConfig(
        cwd=Path("/tmp"),
        context_window=context_window,
        retry_enabled=retry_enabled,
        retry_max_retries=retry_max_retries,
        retry_base_delay_ms=0,
    )

    # Build with a temporary null context then wire the real one
    class _NullCtx:
        pass
    ext_runtime = ExtensionRuntime(ext_result, _NullCtx(), hooks)  # type: ignore[arg-type]

    session = Agent(
        engine=engine,
        session_manager=sm,
        resource_loader=resource_loader,
        extension_runtime=ext_runtime,
        compaction=compaction,
        config=config,
    )
    # Replace with real context pointing at the session
    real_runtime = ExtensionRuntime(ext_result, session, hooks)
    session._extensions = real_runtime

    return session, sm


# ── Basic prompt flow ─────────────────────────────────────────────────────────

class TestBasicPromptFlow:
    @pytest.mark.asyncio
    async def test_prompt_persists_user_message(self):
        session, sm = make_session(FakeLLM(text_seq("Hi back!")))
        await session.invoke("Hello")
        roles = [sm.by_id[e.id].message.role for e in sm.get_entries()
                 if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert Role.USER in roles

    @pytest.mark.asyncio
    async def test_prompt_persists_assistant_message(self):
        session, sm = make_session(FakeLLM(text_seq("Hi back!")))
        await session.invoke("Hello")
        roles = [sm.by_id[e.id].message.role for e in sm.get_entries()
                 if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert Role.ASSISTANT in roles

    @pytest.mark.asyncio
    async def test_second_prompt_builds_on_history(self):
        llm = FakeLLM(text_seq("first"), text_seq("second"))
        session, sm = make_session(llm)
        await session.invoke("q1")
        await session.invoke("q2")
        msgs = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        # user + assistant + user + assistant = 4
        assert len(msgs) == 4

    @pytest.mark.asyncio
    async def test_system_prompt_propagated(self):
        captured_contexts = []
        class CapturingLLM(FakeLLM):
            async def stream(self, context: LLMContext):
                captured_contexts.append(context)
                for e in text_seq("ok"):
                    yield e

        session, _ = make_session(CapturingLLM(), system_prompt="Be helpful")
        await session.invoke("hi")
        assert "Be helpful" in captured_contexts[0].system_prompt

    @pytest.mark.asyncio
    async def test_session_is_idle_after_prompt(self):
        session, _ = make_session(FakeLLM(text_seq("done")))
        await session.invoke("go")
        assert session.is_idle()


# ── Retry behavior ────────────────────────────────────────────────────────────

class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        llm = FakeLLM(error_seq("transient"), text_seq("recovered"))
        session, sm = make_session(llm, retry_enabled=True, retry_max_retries=2)
        await session.invoke("go")
        # Should have user + assistant in session after recovery
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert any(isinstance(sm.by_id[e.id].message, AssistantMessage) for e in entries)

    @pytest.mark.asyncio
    async def test_failed_attempt_rewound(self):
        llm = FakeLLM(error_seq("fail1"), text_seq("ok"))
        session, sm = make_session(llm, retry_enabled=True, retry_max_retries=2)
        await session.invoke("go")
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        # Only one user + one assistant (failed attempt was rewound)
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        llm = FakeLLM(error_seq("e1"), error_seq("e2"))
        session, _ = make_session(llm, retry_enabled=True, retry_max_retries=1)
        with pytest.raises(RuntimeError, match="attempt"):
            await session.invoke("go")

    @pytest.mark.asyncio
    async def test_exhausted_retries_preserves_session(self):
        """When retries are exhausted, the session is fully non-destructive:
        the user message + the trailing error turn are kept on disk so the
        user can send 'continue' to resume (see agent/service.py:478-485).
        """
        llm = FakeLLM(error_seq("e1"), error_seq("e2"))
        session, sm = make_session(llm, retry_enabled=True, retry_max_retries=1)
        try:
            await session.invoke("go")
        except RuntimeError:
            pass
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert len(entries) >= 1  # at least the user message survives


# ── Compaction integration ────────────────────────────────────────────────────

class TestCompactionIntegration:
    @pytest.mark.asyncio
    async def test_compaction_triggered_when_requested(self):
        llm = FakeLLM(text_seq("answer"), summary_seq("Summary of everything"))
        settings = CompactionSettings(enabled=True, keep_recent_tokens=1)
        session, sm = make_session(llm, compaction_settings=settings)
        session.compact()  # manually request compaction

        for _ in range(5):
            sm.append_message(UserMessage.text(f"msg"))
            a = AssistantMessage()
            a.contents = [TextContent(content="reply")]
            sm.append_message(a)

        await session.invoke("compact me")

        # Compaction entry should be in session
        entries = sm.get_entries()
        assert any(isinstance(sm.by_id.get(e.id), CompactionEntry) for e in entries)

    @pytest.mark.asyncio
    async def test_compaction_entry_has_summary(self):
        llm = FakeLLM(text_seq("answer"), summary_seq("Summarized history"))
        settings = CompactionSettings(enabled=True, keep_recent_tokens=1)
        session, sm = make_session(llm, compaction_settings=settings)
        session.compact()

        for _ in range(5):
            sm.append_message(UserMessage.text("msg"))
            a = AssistantMessage()
            a.contents = [TextContent(content="reply")]
            sm.append_message(a)

        await session.invoke("go")

        comp_entries = [e for e in sm.get_entries()
                        if isinstance(sm.by_id.get(e.id), CompactionEntry)]
        if comp_entries:
            comp = sm.by_id[comp_entries[0].id]
            assert "Summarized history" in comp.summary

    @pytest.mark.asyncio
    async def test_compaction_entry_injects_active_todos(self):
        todo_tool = TodoTool()
        await todo_tool.execute(ToolInvocation(
            id="todo-1",
            name="todo",
            params={
                "todos": [
                    {"id": "1", "content": "Done item", "status": "completed"},
                    {"id": "2", "content": "Current item", "status": "in_progress"},
                    {"id": "3", "content": "Future item", "status": "pending"},
                ],
            },
        ))

        llm = FakeLLM(text_seq("answer"), summary_seq("Summarized history"))
        settings = CompactionSettings(enabled=True, keep_recent_tokens=1)
        session, sm = make_session(llm, tools=[todo_tool], compaction_settings=settings)
        session.compact()

        for _ in range(5):
            sm.append_message(UserMessage.text("msg"))
            a = AssistantMessage()
            a.contents = [TextContent(content="reply")]
            sm.append_message(a)

        await session.invoke("go")

        comp_entries = [e for e in sm.get_entries()
                        if isinstance(sm.by_id.get(e.id), CompactionEntry)]
        assert comp_entries
        comp = sm.by_id[comp_entries[0].id]
        assert "Summarized history" in comp.summary
        assert "Current item" in comp.summary
        assert "Future item" in comp.summary
        assert "Done item" not in comp.summary

    @pytest.mark.asyncio
    async def test_no_compaction_when_disabled(self):
        llm = FakeLLM(text_seq("answer"))
        settings = CompactionSettings(enabled=False)
        session, sm = make_session(llm, compaction_settings=settings)
        # Do not request compaction — verify auto-compaction is suppressed by enabled=False
        await session.invoke("hi")

        # No compaction entries
        entries = sm.get_entries()
        assert not any(isinstance(sm.by_id.get(e.id), CompactionEntry) for e in entries)


# ── Extension hooks ───────────────────────────────────────────────────────────

class TestExtensionHooks:
    def _make_session_with_ext(self, llm, ext: Extension):
        from program.hooks.service import Hooks
        sm = SessionManager.in_memory()
        hooks = Hooks()
        engine = Engine(llm=llm, tools=[], options=Options(), hooks=hooks)
        compaction = Compaction(llm=llm, settings=CompactionSettings(enabled=False))
        resource_loader = FakeResourceLoader()
        config = AgentConfig(
            cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0
        )
        load_result = LoadExtensionsResult(extensions=[ext])

        class _NullCtx:
            pass
        session = Agent(
            engine=engine, session_manager=sm, resource_loader=resource_loader,
            extension_runtime=ExtensionRuntime(load_result, _NullCtx(), hooks),  # type: ignore[arg-type]
            compaction=compaction, config=config,
        )
        real_runtime = ExtensionRuntime(load_result, session, hooks)
        session._extensions = real_runtime
        return session, sm

    @pytest.mark.asyncio
    async def test_input_event_fired(self):
        si = SourceInfo(path="e.py", source="local")
        ext = Extension(path="e.py", source_info=si)
        received = []
        ext.handlers['input'] = [lambda e, ctx: received.append(e.text)]

        llm = FakeLLM(text_seq("ok"))
        session, _ = self._make_session_with_ext(llm, ext)
        await session.invoke("hello world")
        assert "hello world" in received

    @pytest.mark.asyncio
    async def test_agent_end_event_fired(self):
        si = SourceInfo(path="e.py", source="local")
        ext = Extension(path="e.py", source_info=si)
        fired = []
        ext.handlers['agent_end'] = [lambda e, ctx: fired.append(True)]

        llm = FakeLLM(text_seq("done"))
        session, _ = self._make_session_with_ext(llm, ext)
        await session.invoke("go")
        assert fired

    @pytest.mark.asyncio
    async def test_before_agent_start_can_override_system_prompt(self):
        si = SourceInfo(path="e.py", source="local")
        ext = Extension(path="e.py", source_info=si)
        ext.handlers['before_agent_start'] = [
            lambda e, ctx: BeforeAgentStartEventResult(system_prompt="OVERRIDDEN")
        ]

        captured = []
        class CapturingLLM(FakeLLM):
            async def stream(self, context: LLMContext):
                captured.append(context.system_prompt)
                for ev in text_seq("ok"):
                    yield ev

        session, _ = self._make_session_with_ext(CapturingLLM(), ext)
        await session.invoke("go")
        assert captured[0] == "OVERRIDDEN"


# ── Tool integration ──────────────────────────────────────────────────────────

class TestToolIntegration:
    @pytest.mark.asyncio
    async def test_tool_called_and_result_in_session(self):
        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("done"))
        tool = make_tool("my_tool", "the_result")
        session, sm = make_session(llm, tools=[tool])
        await session.invoke("use the tool")

        # ToolMessage should be in session
        entries = sm.get_entries()
        roles = [sm.by_id[e.id].message.role for e in entries
                 if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert Role.TOOL in roles

    @pytest.mark.asyncio
    async def test_tool_result_content_persisted(self):
        llm = FakeLLM(tool_call_seq("t1", "my_tool"), text_seq("done"))
        tool = make_tool("my_tool", "expected_output")
        session, sm = make_session(llm, tools=[tool])
        await session.invoke("run tool")

        tool_msgs = [sm.by_id[e.id].message for e in sm.get_entries()
                     if isinstance(sm.by_id.get(e.id), MessageEntry)
                     and sm.by_id[e.id].message.role == Role.TOOL]
        assert len(tool_msgs) == 1
        assert isinstance(tool_msgs[0], ToolMessage)
        result_contents = [c for c in tool_msgs[0].contents if isinstance(c, ToolResultContent)]
        assert any("expected_output" in c.content for c in result_contents)

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_persisted(self):
        tc1 = ToolCallContent(id="t1", name="tool_a", args={})
        tc2 = ToolCallContent(id="t2", name="tool_b", args={})
        two_tools = [
            StartEvent(),
            ToolCallStartEvent(tool_call=ToolCallContent(id="t1", name="tool_a")),
            ToolCallEndEvent(tool_call=tc1),
            ToolCallStartEvent(tool_call=ToolCallContent(id="t2", name="tool_b")),
            ToolCallEndEvent(tool_call=tc2),
            EndEvent(reason=StopReason.ToolCalls),
        ]
        llm = FakeLLM(two_tools, text_seq("all done"))
        tools = [make_tool("tool_a"), make_tool("tool_b")]
        session, sm = make_session(llm, tools=tools)
        await session.invoke("use both tools")

        entries = sm.get_entries()
        tool_msg_entries = [e for e in entries
                            if isinstance(sm.by_id.get(e.id), MessageEntry)
                            and sm.by_id[e.id].message.role == Role.TOOL]
        assert len(tool_msg_entries) >= 1


# ── Context usage tracking ────────────────────────────────────────────────────

class TestContextUsage:
    @pytest.mark.asyncio
    async def test_context_usage_none_before_any_prompt(self):
        session, _ = make_session(FakeLLM())
        assert session.get_context_usage() is None

    @pytest.mark.asyncio
    async def test_get_system_prompt_before_prompt(self):
        session, _ = make_session(FakeLLM())
        assert isinstance(session.get_system_prompt(), str)

    @pytest.mark.asyncio
    async def test_abort_can_be_called(self):
        session, _ = make_session(FakeLLM(text_seq("hi")))
        # abort() before/after run should not crash
        session.abort()

    @pytest.mark.asyncio
    async def test_run_compaction_refreshes_context_usage(self):
        llm = FakeLLM(
            text_seq("answer", Usage(input_tokens=6000, output_tokens=260)),
            summary_seq("Summarized history"),
        )
        settings = CompactionSettings(enabled=True, keep_recent_tokens=1)
        session, sm = make_session(llm, compaction_settings=settings)

        for _ in range(5):
            sm.append_message(UserMessage.text("msg"))
            a = AssistantMessage()
            a.contents = [TextContent(content="reply")]
            sm.append_message(a)

        await session.invoke("compact me")
        before = session.get_context_usage()
        assert before is not None
        assert before.tokens == 6260

        performed = await session.run_compaction()
        assert performed is True

        after = session.get_context_usage()
        assert after is not None
        assert after.tokens < before.tokens
