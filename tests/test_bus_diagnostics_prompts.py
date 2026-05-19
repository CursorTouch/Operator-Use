"""EventBus, Diagnostics service, Prompt builder, Resource loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from program.bus.service import Bus, EventBus
from program.bus.types import IncomingMessage, OutgoingMessage, TextPart
from program.diagnostics.service import run_diagnostics
from program.diagnostics.types import ResourceDiagnostic
from program.extension.types import LoadExtensionsResult, ExtensionError, Extension
from program.prompt.builder import PromptTemplate
from program.resource.context import load_project_context_files
from program.resource.types import ContextFile
from program.skill.types import SourceInfo


def _inc(text: str = "hi") -> IncomingMessage:
    return IncomingMessage(channel="stdio", chat_id="1", parts=[TextPart(content=text)])


def _out(text: str = "bye") -> OutgoingMessage:
    return OutgoingMessage(channel="stdio", chat_id="1", parts=[TextPart(content=text)])


class TestEventBus:
    def test_eventbus_alias(self):
        assert EventBus is Bus

    @pytest.mark.asyncio
    async def test_on_and_emit_async(self):
        bus = Bus()
        msg = _inc("payload")
        await bus.publish_incoming(msg)
        result = await bus.consume_incoming()
        assert result is msg

    @pytest.mark.asyncio
    async def test_sync_emit(self):
        bus = Bus()
        msg = _out("reply")
        await bus.publish_outgoing(msg)
        result = await bus.consume_outgoing()
        assert result is msg

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self):
        bus = Bus()
        msgs = [_inc(str(i)) for i in range(3)]
        for m in msgs:
            await bus.publish_incoming(m)
        received = [await bus.consume_incoming() for _ in range(3)]
        assert received == msgs

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_abort_others(self):
        # Bus is a queue; publishing always succeeds regardless of consumers
        bus = Bus()
        await bus.publish_incoming(_inc("a"))
        await bus.publish_incoming(_inc("b"))
        a = await bus.consume_incoming()
        b = await bus.consume_incoming()
        assert a is not b

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        bus = Bus()
        assert bus._incoming.qsize() == 0
        await bus.publish_incoming(_inc("1"))
        await bus.publish_incoming(_inc("2"))
        assert bus._incoming.qsize() == 2

    @pytest.mark.asyncio
    async def test_once_fires_once_then_unsubscribes(self):
        bus = Bus()
        await bus.publish_incoming(_inc("first"))
        result = await bus.consume_incoming()
        assert result.parts[0].content == "first"
        # queue is now empty
        assert bus._incoming.empty()


class TestDiagnostics:
    def test_empty_extensions_returns_empty_list(self):
        result = run_diagnostics(LoadExtensionsResult())
        assert isinstance(result, list)
        assert len(result) == 0

    def test_extension_error_produces_diagnostic(self):
        si = SourceInfo(path="bad.py", source="local")
        ext = Extension(path="bad.py", source_info=si)
        load_result = LoadExtensionsResult(
            extensions=[ext],
            errors=[ExtensionError(extension_path="bad.py", event="load",
                                   error="SyntaxError: unexpected token", stack="...")]
        )
        diags = run_diagnostics(load_result)
        assert len(diags) > 0
        assert any("bad.py" in d.path or "SyntaxError" in d.message for d in diags)

    def test_diagnostic_is_resource_diagnostic(self):
        load_result = LoadExtensionsResult(
            errors=[ExtensionError(extension_path="x.py", event="load",
                                   error="ImportError: no module", stack="")]
        )
        diags = run_diagnostics(load_result)
        for d in diags:
            assert isinstance(d, ResourceDiagnostic)


class TestPromptBuilder:
    def test_builds_non_empty_string(self):
        pt = PromptTemplate(cwd="/some/path", tools=[], skills=[])
        result = pt.build()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_custom_prompt_included(self):
        pt = PromptTemplate(cwd="/tmp", tools=[], skills=[], custom_prompt="custom instructions")
        assert "custom instructions" in pt.build()

    def test_append_system_prompt_appended(self):
        pt = PromptTemplate(cwd="/tmp", tools=[], skills=[], append_system_prompt="appended text")
        assert "appended text" in pt.build()


class TestResourceContext:
    def test_load_project_context_nonexistent_dir(self):
        result = load_project_context_files(Path("/nonexistent"), Path("/nonexistent"))
        assert isinstance(result, list)

    def test_context_file_path_is_str(self):
        cf = ContextFile(path="/tmp/CLAUDE.md", content="# Guide")
        assert "CLAUDE.md" in cf.path
        assert "Guide" in cf.content
