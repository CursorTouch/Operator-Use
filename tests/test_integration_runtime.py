"""Integration tests for Runtime: user_input routing, session lifecycle."""
import pytest
from pathlib import Path
from typing import AsyncIterator

from operator_use.runtime.service import Runtime
from operator_use.runtime.types import RuntimeContext
from operator_use.agent.service import Agent
from operator_use.agent.types import AgentConfig
from operator_use.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from operator_use.compaction.strategy.types import CompactionSettings
from operator_use.commands.registry import CommandRegistry
from operator_use.commands.types import SlashCommandInfo
from operator_use.engine.service import Engine
from operator_use.engine.types import Options
from operator_use.extension.runtime import ExtensionRuntime
from operator_use.extension.types import LoadExtensionsResult, Extension, SessionStartEvent, SessionShutdownEvent
from operator_use.hooks.types import SessionBeforeCompactEvent, SessionCompactEvent
from operator_use.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
)
from operator_use.message.types import TextContent, UserMessage, AssistantMessage, Role
from operator_use.resource.types import BaseResourceLoader, ResourceExtensionPaths
from operator_use.session.manager import SessionManager
from operator_use.session.types import MessageEntry, CompactionEntry
from operator_use.skill.types import SourceInfo


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._seqs[self._idx % len(self._seqs)]
        self._idx += 1
        for e in events:
            yield e

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        events = self._seqs[self._idx % len(self._seqs)]
        self._idx += 1
        return events


def text_seq(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


# ── Fake resource loader ──────────────────────────────────────────────────────

class FakeResourceLoader(BaseResourceLoader):
    def get_extensions(self): return LoadExtensionsResult()
    def get_skills(self): return [], []
    def get_tools(self): return []
    def get_commands(self): return []
    def get_hooks(self): return []
    def get_subagent_profiles(self): return []
    def get_agent_profiles(self): return []
    def set_active_profile(self, profile): pass
    def get_context_files(self): return []
    def get_system_prompt(self): return None
    def get_append_system_prompt(self): return []
    def get_soul_prompt(self): return None
    def get_user_profile(self): return None
    def get_agent_memory(self): return None
    def extend_resources(self, paths): pass
    def get_diagnostics(self, runtime=None): return []
    async def reload(self): pass


# ── Runtime factory ───────────────────────────────────────────────────────────

def make_runtime(llm: FakeLLM, extensions: list[Extension] | None = None) -> Runtime:
    sm = SessionManager.in_memory()
    engine = Engine(llm=llm, tools=[], options=Options())
    compaction = Compaction(llm=llm, settings=CompactionSettings(enabled=False))
    resource_loader = FakeResourceLoader()
    config = AgentConfig(
        cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0
    )
    load_result = LoadExtensionsResult(extensions=extensions or [])

    class _NullCtx:
        pass

    agent = Agent(
        engine=engine, session_manager=sm, resource_loader=resource_loader,
        extension_runtime=ExtensionRuntime(load_result, _NullCtx()),  # type: ignore
        compaction=compaction, config=config,
    )
    real_runtime = ExtensionRuntime(load_result, agent)
    agent._extensions = real_runtime

    class _FakeServices:
        agent = None
        session_manager = sm
        extension_runtime = real_runtime

    _FakeServices.agent = agent

    rt = Runtime.__new__(Runtime)
    rt._context = _FakeServices()
    rt._config = None
    rt.commands = CommandRegistry(runtime=rt)
    rt.commands.register_from_extensions(real_runtime.get_commands())
    return rt


# ── user_input routing ────────────────────────────────────────────────────────

class TestHandleInputRouting:
    @pytest.mark.asyncio
    async def test_plain_text_routes_to_prompt(self):
        llm = FakeLLM(text_seq("hi"))
        rt = make_runtime(llm)
        await rt.user_input("hello there")
        sm = rt.session_manager
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert any(sm.by_id[e.id].message.role == Role.USER for e in entries)

    @pytest.mark.asyncio
    async def test_slash_help_dispatched(self, capsys):
        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm)
        await rt.user_input("/help")
        out = capsys.readouterr().out
        assert "compact" in out.lower() or "help" in out.lower()

    @pytest.mark.asyncio
    async def test_unknown_command_handled(self, capsys):
        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm)
        await rt.user_input("/nosuchcommand")
        out = capsys.readouterr().out
        assert "Unknown" in out

    @pytest.mark.asyncio
    async def test_slash_with_args_parsed(self):
        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm)
        dispatched_args = []

        async def handler(registry, args):
            dispatched_args.extend(args)

        rt.commands.register(SlashCommandInfo(name="testcmd", description="t", handler=handler))
        await rt.user_input("/testcmd arg1 arg2")
        assert dispatched_args == ["arg1", "arg2"]


# ── Current session access ────────────────────────────────────────────────────

class TestCurrentSession:
    def test_current_session_is_set(self):
        llm = FakeLLM(text_seq("hi"))
        rt = make_runtime(llm)
        assert rt.current_session is not None

    def test_session_manager_accessible(self):
        llm = FakeLLM(text_seq("hi"))
        rt = make_runtime(llm)
        assert rt.session_manager is not None

    @pytest.mark.asyncio
    async def test_prompt_on_runtime_works(self):
        llm = FakeLLM(text_seq("response"))
        rt = make_runtime(llm)
        await rt.invoke("direct prompt")
        sm = rt.session_manager
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_compact_command_runs_immediately_and_emits_events(self):
        llm = FakeLLM(text_seq("response"), text_seq("summary"))
        rt = make_runtime(llm)
        seen: list[str] = []
        rt.current_session.hooks.subscribe(lambda e: seen.append(type(e).__name__))

        await rt.invoke("direct prompt")
        await rt.user_input("/compact")

        assert "SessionBeforeCompactEvent" in seen
        assert "SessionCompactEvent" in seen
        assert any(isinstance(e, CompactionEntry) for e in rt.session_manager.get_entries())


# ── Extension command registration ───────────────────────────────────────────

class TestExtensionCommands:
    @pytest.mark.asyncio
    async def test_extension_command_registered_on_init(self):
        from operator_use.extension.types import RegisteredCommand
        from operator_use.skill.types import SourceInfo

        si = SourceInfo(path="ext.py", source="local")
        ext = Extension(path="ext.py", source_info=si)
        called = []

        async def handler(reg, args):
            called.append(True)

        ext.commands['myextcmd'] = RegisteredCommand(
            name='myextcmd', source_info=si, description='Test', handler=handler
        )

        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm, extensions=[ext])
        # Command should be registered
        assert rt.commands.get('myextcmd') is not None

    @pytest.mark.asyncio
    async def test_extension_command_dispatched(self):
        from operator_use.extension.types import RegisteredCommand

        si = SourceInfo(path="ext.py", source="local")
        ext = Extension(path="ext.py", source_info=si)
        called = []

        async def handler(reg, args):
            called.append(args)

        ext.commands['extcmd'] = RegisteredCommand(
            name='extcmd', source_info=si, description='Test', handler=handler
        )

        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm, extensions=[ext])
        await rt.user_input("/extcmd foo bar")
        assert called == [['foo', 'bar']]


# ── Session lifecycle events ──────────────────────────────────────────────────

class TestLifecycleEvents:
    @pytest.mark.asyncio
    async def test_extension_sees_session_start(self):
        si = SourceInfo(path="e.py", source="local")
        ext = Extension(path="e.py", source_info=si)
        events = []
        ext.handlers['session_start'] = [lambda e, ctx: events.append(e.reason)]

        llm = FakeLLM(text_seq("ok"))
        rt = make_runtime(llm, extensions=[ext])
        # Manually fire session_start (normally done by Runtime.create)
        await rt._context.extension_runtime.emit(
            'session_start', SessionStartEvent(reason='startup')
        )
        assert 'startup' in events

    @pytest.mark.asyncio
    async def test_session_shutdown_event_emitted(self):
        si = SourceInfo(path="e.py", source="local")
        ext = Extension(path="e.py", source_info=si)
        shutdown_events = []
        ext.handlers['session_shutdown'] = [lambda e, ctx: shutdown_events.append(e.reason)]

        llm = FakeLLM(text_seq("ok"))
        rt = make_runtime(llm, extensions=[ext])
        await rt._context.extension_runtime.emit(
            'session_shutdown', SessionShutdownEvent(reason='quit')
        )
        assert 'quit' in shutdown_events


# ── Multi-turn conversation context ──────────────────────────────────────────

class TestMultiTurnContext:
    @pytest.mark.asyncio
    async def test_history_included_in_second_turn(self):
        captured_contexts = []

        class CapturingLLM:
            _seqs = [text_seq("first"), text_seq("second")]
            _idx = 0

            async def stream(self, context: LLMContext):
                captured_contexts.append(list(context.messages))
                for e in self._seqs[self._idx]:
                    yield e
                self._idx += 1

            async def invoke(self, context: LLMContext, thinking_level=None):
                return self._seqs[self._idx]

        llm = CapturingLLM()
        sm = SessionManager.in_memory()
        engine = Engine(llm=llm, tools=[], options=Options())
        compaction = Compaction(llm=llm, settings=CompactionSettings(enabled=False))

        config = AgentConfig(
            cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0
        )
        load_result = LoadExtensionsResult()

        class _NullCtx:
            pass

        session = Agent(
            engine=engine, session_manager=sm, resource_loader=FakeResourceLoader(),
            extension_runtime=ExtensionRuntime(load_result, _NullCtx()),  # type: ignore
            compaction=compaction, config=config,
        )
        session._extensions = ExtensionRuntime(load_result, session)

        await session.invoke("first question")
        await session.invoke("second question")

        # Second call should include prior user+assistant messages
        assert len(captured_contexts[1]) > len(captured_contexts[0])
