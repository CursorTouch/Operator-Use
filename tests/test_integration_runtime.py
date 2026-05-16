"""Integration tests for AgentSessionRuntime: handle_input routing, session lifecycle."""
import pytest
from pathlib import Path
from typing import AsyncIterator

from program.agent_session.runtime import AgentSessionRuntime
from program.agent_session.services import AgentSessionServices, AgentSessionServicesConfig
from program.agent_session.session import AgentSession
from program.agent_session.types import AgentSessionConfig
from program.compaction.compact import Compaction
from program.compaction.types import CompactionSettings
from program.commands.registry import CommandRegistry
from program.commands.types import SlashCommandInfo
from program.engine.loop import AgentLoop
from program.engine.types import Options
from program.extension.runtime import ExtensionRuntime
from program.extension.types import LoadExtensionsResult, Extension, SessionStartEvent, SessionShutdownEvent
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
)
from program.message.types import TextContent, UserMessage, AssistantMessage, Role
from program.resource.types import BaseResourceLoader, ResourceExtensionPaths
from program.session.manager import SessionManager
from program.session.types import MessageEntry
from program.skill.types import SourceInfo


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
    def get_context_files(self): return []
    def get_system_prompt(self): return None
    def get_append_system_prompt(self): return []
    def extend_resources(self, paths): pass
    def get_diagnostics(self, runtime=None): return []
    async def reload(self): pass


# ── Runtime factory ───────────────────────────────────────────────────────────

def make_runtime(llm: FakeLLM, extensions: list[Extension] | None = None) -> AgentSessionRuntime:
    sm = SessionManager.in_memory()
    loop = AgentLoop(llm=llm, tools=[], options=Options())
    compaction = Compaction(llm=llm, settings=CompactionSettings(enabled=False))
    resource_loader = FakeResourceLoader()
    config = AgentSessionConfig(
        cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0
    )
    load_result = LoadExtensionsResult(extensions=extensions or [])

    class _NullCtx:
        pass

    session = AgentSession(
        loop=loop, session_manager=sm, resource_loader=resource_loader,
        extension_runtime=ExtensionRuntime(load_result, _NullCtx()),  # type: ignore
        compaction=compaction, config=config,
    )
    real_runtime = ExtensionRuntime(load_result, session)
    session._extensions = real_runtime

    class _FakeServices:
        session = None
        session_manager = sm
        extension_runtime = real_runtime

    _FakeServices.session = session

    rt = AgentSessionRuntime.__new__(AgentSessionRuntime)
    rt._services = _FakeServices()
    rt._config = None
    rt.commands = CommandRegistry(runtime=rt)
    rt.commands.register_from_extensions(real_runtime.get_commands())
    return rt


# ── handle_input routing ──────────────────────────────────────────────────────

class TestHandleInputRouting:
    @pytest.mark.asyncio
    async def test_plain_text_routes_to_prompt(self):
        llm = FakeLLM(text_seq("hi"))
        rt = make_runtime(llm)
        await rt.handle_input("hello there")
        sm = rt.session_manager
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert any(sm.by_id[e.id].message.role == Role.USER for e in entries)

    @pytest.mark.asyncio
    async def test_slash_help_dispatched(self, capsys):
        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm)
        await rt.handle_input("/help")
        out = capsys.readouterr().out
        assert "compact" in out.lower() or "help" in out.lower()

    @pytest.mark.asyncio
    async def test_unknown_command_handled(self, capsys):
        llm = FakeLLM(text_seq("unused"))
        rt = make_runtime(llm)
        await rt.handle_input("/nosuchcommand")
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
        await rt.handle_input("/testcmd arg1 arg2")
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
        await rt.prompt("direct prompt")
        sm = rt.session_manager
        entries = [e for e in sm.get_entries() if isinstance(sm.by_id.get(e.id), MessageEntry)]
        assert len(entries) >= 1


# ── Extension command registration ───────────────────────────────────────────

class TestExtensionCommands:
    @pytest.mark.asyncio
    async def test_extension_command_registered_on_init(self):
        from program.extension.types import RegisteredCommand
        from program.skill.types import SourceInfo

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
        from program.extension.types import RegisteredCommand

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
        await rt.handle_input("/extcmd foo bar")
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
        # Manually fire session_start (normally done by AgentSessionRuntime.create)
        await rt._services.extension_runtime.emit(
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
        await rt._services.extension_runtime.emit(
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
        loop = AgentLoop(llm=llm, tools=[], options=Options())
        compaction = Compaction(llm=llm, settings=CompactionSettings(enabled=False))

        config = AgentSessionConfig(
            cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0
        )
        load_result = LoadExtensionsResult()

        class _NullCtx:
            pass

        session = AgentSession(
            loop=loop, session_manager=sm, resource_loader=FakeResourceLoader(),
            extension_runtime=ExtensionRuntime(load_result, _NullCtx()),  # type: ignore
            compaction=compaction, config=config,
        )
        session._extensions = ExtensionRuntime(load_result, session)

        await session.prompt("first question")
        await session.prompt("second question")

        # Second call should include prior user+assistant messages
        assert len(captured_contexts[1]) > len(captured_contexts[0])
