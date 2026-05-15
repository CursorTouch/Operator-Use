"""Tests for extension loader: loading, error handling, tool/command registration."""
import pytest
import tempfile
from pathlib import Path

from program.extension.loader import load_extension_from_file, discover_and_load_extensions
from program.extension.types import (
    Extension, ExtensionAPI, ExtensionError, LoadExtensionsResult,
    ToolDefinition, RegisteredTool,
)
from program.bus.service import EventBus
from program.tool.types import ToolResult, ToolExecutionMode
from pydantic import BaseModel


def write_ext(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ── load_extension_from_file ──────────────────────────────────────────────────

class TestLoadExtensionFromFile:
    @pytest.mark.asyncio
    async def test_loads_valid_extension(self, tmp_path):
        p = write_ext(tmp_path, "my_ext.py", """
def extension(api):
    pass
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []

    @pytest.mark.asyncio
    async def test_error_if_no_extension_callable(self, tmp_path):
        p = write_ext(tmp_path, "bad.py", "x = 1")
        ext, errors = await load_extension_from_file(p)
        assert ext is None
        assert len(errors) == 1
        assert 'extension' in errors[0].error

    @pytest.mark.asyncio
    async def test_error_if_file_raises_on_import(self, tmp_path):
        p = write_ext(tmp_path, "crash.py", "raise RuntimeError('boom')")
        ext, errors = await load_extension_from_file(p)
        assert ext is None
        assert len(errors) == 1
        assert errors[0].stack is not None

    @pytest.mark.asyncio
    async def test_async_factory_awaited(self, tmp_path):
        p = write_ext(tmp_path, "async_ext.py", """
async def extension(api):
    pass
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []

    @pytest.mark.asyncio
    async def test_extension_registers_handler(self, tmp_path):
        p = write_ext(tmp_path, "handler_ext.py", """
def extension(api):
    api.on('session_start', lambda e, ctx: None)
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert 'session_start' in ext.handlers
        assert len(ext.handlers['session_start']) == 1

    @pytest.mark.asyncio
    async def test_extension_registers_tool(self, tmp_path):
        p = write_ext(tmp_path, "tool_ext.py", """
from program.extension.types import ToolDefinition
from program.tool.types import ToolResult
from pydantic import BaseModel

class Params(BaseModel):
    pass

async def my_execute(invocation, **kwargs):
    return ToolResult.ok(invocation.id, "done")

def extension(api):
    api.register_tool(ToolDefinition(
        name='my_tool',
        description='A test tool',
        parameters=Params,
        execute=my_execute,
    ))
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert 'my_tool' in ext.tools

    @pytest.mark.asyncio
    async def test_extension_registers_command(self, tmp_path):
        p = write_ext(tmp_path, "cmd_ext.py", """
async def handle(registry, args):
    pass

def extension(api):
    api.register_command('do_thing', handle, description='Does a thing')
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert 'do_thing' in ext.commands
        assert ext.commands['do_thing'].description == 'Does a thing'

    @pytest.mark.asyncio
    async def test_error_captures_extension_path(self, tmp_path):
        p = write_ext(tmp_path, "crash2.py", "raise ValueError('bad')")
        ext, errors = await load_extension_from_file(p)
        assert errors[0].extension_path == str(p)

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error(self, tmp_path):
        p = tmp_path / "ghost.py"
        ext, errors = await load_extension_from_file(p)
        assert ext is None
        assert len(errors) == 1


# ── discover_and_load_extensions ─────────────────────────────────────────────

class TestDiscoverAndLoad:
    @pytest.mark.asyncio
    async def test_discovers_all_py_files(self, tmp_path):
        write_ext(tmp_path, "a.py", "def extension(api): pass")
        write_ext(tmp_path, "b.py", "def extension(api): pass")
        result = await discover_and_load_extensions([tmp_path])
        assert len(result.extensions) == 2

    @pytest.mark.asyncio
    async def test_skips_underscore_files(self, tmp_path):
        write_ext(tmp_path, "_private.py", "def extension(api): pass")
        write_ext(tmp_path, "public.py", "def extension(api): pass")
        result = await discover_and_load_extensions([tmp_path])
        assert len(result.extensions) == 1

    @pytest.mark.asyncio
    async def test_missing_dir_is_skipped(self, tmp_path):
        missing = tmp_path / "no_such_dir"
        result = await discover_and_load_extensions([missing])
        assert result.extensions == []
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_errors_collected_not_raised(self, tmp_path):
        write_ext(tmp_path, "ok.py", "def extension(api): pass")
        write_ext(tmp_path, "bad.py", "raise SyntaxError('oops')")
        result = await discover_and_load_extensions([tmp_path])
        assert len(result.extensions) == 1
        assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_shared_event_bus_passed(self, tmp_path):
        bus = EventBus()
        write_ext(tmp_path, "c.py", "def extension(api): pass")
        result = await discover_and_load_extensions([tmp_path], bus=bus)
        assert len(result.extensions) == 1

    @pytest.mark.asyncio
    async def test_multiple_dirs(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        write_ext(dir1, "e1.py", "def extension(api): pass")
        write_ext(dir2, "e2.py", "def extension(api): pass")
        result = await discover_and_load_extensions([dir1, dir2])
        assert len(result.extensions) == 2
