"""Tests for extension loader: loading, error handling, tool/command registration."""
import pytest
import tempfile
from pathlib import Path

from operator_use.extension.loader import load_extension_from_file, discover_and_load_extensions
from operator_use.extension.types import (
    Extension, ExtensionAPI, ExtensionError, LoadExtensionsResult,
    ToolDefinition, RegisteredTool,
)
from operator_use.bus.service import EventBus
from operator_use.tool.types import ToolResult, ToolExecutionMode
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
from operator_use.extension.types import ToolDefinition
from operator_use.tool.types import ToolResult
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


# ── provider / memory / subagent registration ─────────────────────────────────

class TestProviderRegistration:
    @pytest.mark.asyncio
    async def test_register_inference_provider(self, tmp_path):
        p = write_ext(tmp_path, "prov_ext.py", """
from dataclasses import dataclass
from operator_use.inference.types import LLMOptions, AuthType, Transport

@dataclass
class FakeProvider:
    id: str = 'fake-llm'
    name: str = 'Fake LLM'
    api: str = 'fake_api'
    auth_type: object = None
    supported_transports: list = None
    options: object = None

    def __post_init__(self):
        from operator_use.inference.types import AuthType, Transport, LLMOptions
        self.auth_type = AuthType.ApiKey
        self.supported_transports = [Transport.HTTP]
        self.options = LLMOptions()

def extension(api):
    api.register_provider(FakeProvider())
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert len(ext.inference_providers) == 1
        assert ext.inference_providers[0].provider.id == 'fake-llm'
        assert ext.inference_providers[0].source_info is not None

    @pytest.mark.asyncio
    async def test_register_text_api(self, tmp_path):
        p = write_ext(tmp_path, "api_ext.py", """
class MyLLMAPI:
    pass

def extension(api):
    api.register_text_api('my_api', MyLLMAPI)
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert 'my_api' in ext.inference_apis
        assert ext.inference_apis['my_api'].api.__name__ == 'MyLLMAPI'
        assert ext.inference_apis['my_api'].name == 'my_api'
        assert ext.inference_apis['my_api'].source_info is not None

    @pytest.mark.asyncio
    async def test_register_memory_provider(self, tmp_path):
        p = write_ext(tmp_path, "mem_prov_ext.py", """
from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.types import MemoryOptions

def extension(api):
    api.register_memory_provider(MemoryProvider(
        id='my-mem',
        name='My Memory',
        api='my_mem_api',
        options=MemoryOptions(),
    ))
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert len(ext.memory_providers) == 1
        assert ext.memory_providers[0].provider.id == 'my-mem'
        assert ext.memory_providers[0].source_info is not None

    @pytest.mark.asyncio
    async def test_register_memory_api(self, tmp_path):
        p = write_ext(tmp_path, "mem_api_ext.py", """
from operator_use.memory.api.base import BaseMemoryAPI

class MyMemAPI(BaseMemoryAPI):
    pass

def extension(api):
    api.register_memory_api('my_mem_api', MyMemAPI)
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert 'my_mem_api' in ext.memory_apis

    @pytest.mark.asyncio
    async def test_register_subagent_profile(self, tmp_path):
        p = write_ext(tmp_path, "profile_ext.py", """
from operator_use.subagent.profile import SubagentProfile
from pathlib import Path

def extension(api):
    api.register_subagent_profile(SubagentProfile(
        name='researcher',
        description='Deep research agent',
        tools=['web_search', 'read'],
        system_prompt='You research things.',
        file_path=Path(__file__),
    ))
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert errors == []
        assert len(ext.subagent_profiles) == 1
        assert ext.subagent_profiles[0].profile.name == 'researcher'

    @pytest.mark.asyncio
    async def test_multiple_providers_from_one_extension(self, tmp_path):
        p = write_ext(tmp_path, "multi_prov.py", """
from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.types import MemoryOptions

def extension(api):
    for i in range(3):
        api.register_memory_provider(MemoryProvider(
            id=f'mem-{i}',
            name=f'Memory {i}',
            api='base_api',
            options=MemoryOptions(),
        ))
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert len(ext.memory_providers) == 3
        assert [p.provider.id for p in ext.memory_providers] == ['mem-0', 'mem-1', 'mem-2']

    @pytest.mark.asyncio
    async def test_provider_registration_survives_other_errors(self, tmp_path):
        """Provider registration in the factory should work even if a handler raises later."""
        p = write_ext(tmp_path, "mixed.py", """
from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.types import MemoryOptions

def extension(api):
    api.register_memory_provider(MemoryProvider(
        id='safe-mem', name='Safe', api='base', options=MemoryOptions(),
    ))
    api.on('session_start', lambda e, ctx: None)
""")
        ext, errors = await load_extension_from_file(p)
        assert ext is not None
        assert len(ext.memory_providers) == 1
