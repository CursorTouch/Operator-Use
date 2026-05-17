"""EventBus, Diagnostics service, Prompt builder, Resource loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from program.bus.service import EventBus
from program.diagnostics.service import run_diagnostics
from program.diagnostics.types import ResourceDiagnostic
from program.extension.types import LoadExtensionsResult, ExtensionError, Extension
from program.prompt.builder import PromptTemplate
from program.resource.context import load_project_context_files
from program.resource.types import ContextFile
from program.skill.types import SourceInfo


class TestEventBus:
    @pytest.mark.asyncio
    async def test_on_and_emit_async(self):
        bus = EventBus()
        received = []
        unsub = bus.on("ch", lambda d: received.append(d))
        await bus.emit_async("ch", {"key": "val"})
        assert received[0] == {"key": "val"}
        unsub()
        received.clear()
        await bus.emit_async("ch", "after_unsub")
        assert not received

    def test_sync_emit(self):
        bus = EventBus()
        log = []
        bus.on("ev", lambda d: log.append(d))
        bus.emit("ev", "payload")
        assert "payload" in log

    def test_multiple_subscribers_all_receive(self):
        bus = EventBus()
        log = []
        bus.on("ev", lambda d: log.append("A"))
        bus.on("ev", lambda d: log.append("B"))
        bus.emit("ev", None)
        assert "A" in log and "B" in log

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_abort_others(self):
        bus = EventBus()
        received = []
        async def bad(d): raise RuntimeError("crash")
        async def good(d): received.append(d)
        bus.on("ev", bad)
        bus.on("ev", good)
        await bus.emit_async("ev", "test")
        assert received  # good handler still ran

    def test_subscriber_count(self):
        bus = EventBus()
        assert bus.subscriber_count("ch") == 0
        bus.on("ch", lambda d: None)
        bus.on("ch", lambda d: None)
        assert bus.subscriber_count("ch") == 2

    def test_once_fires_once_then_unsubscribes(self):
        bus = EventBus()
        log = []
        bus.once("ev", lambda d: log.append(d))
        bus.emit("ev", "first")
        bus.emit("ev", "second")
        assert len(log) == 1 and log[0] == "first"


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
