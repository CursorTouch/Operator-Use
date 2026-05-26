"""Tests for ExtensionRuntime: emit, emit_parallel, tools, commands, errors."""
import pytest
from pathlib import Path

from program.extension.runtime import ExtensionRuntime
from program.extension.types import (
    Extension, ExtensionContext, ExtensionError, LoadExtensionsResult,
    ToolDefinition, RegisteredTool, RegisteredCommand, ContextUsage, CompactOptions,
)
from program.skill.types import SourceInfo
from program.tool.types import ToolResult


# ── Fake ExtensionContext ─────────────────────────────────────────────────────

class FakeContext(ExtensionContext):
    @property
    def cwd(self): return Path(".")
    @property
    def session_manager(self): return None
    @property
    def model(self): return None
    @property
    def model_registry(self): return None
    @property
    def signal(self): return None
    def is_idle(self): return True
    def has_pending_messages(self): return False
    def abort(self): pass
    def shutdown(self): pass
    def get_context_usage(self): return None
    def get_system_prompt(self): return ""
    def compact(self, options=None): pass
    async def reload(self): pass
    async def wait_for_idle(self): pass
    async def new_session(self): pass
    async def fork(self, entry_id): pass
    async def switch_session(self, session_file): pass


def make_ext(path: str = "test.py") -> Extension:
    si = SourceInfo(path=path, source='local')
    return Extension(path=path, source_info=si)


def make_runtime(*extensions: Extension) -> ExtensionRuntime:
    result = LoadExtensionsResult(extensions=list(extensions))
    return ExtensionRuntime(result, FakeContext())


# ── emit ──────────────────────────────────────────────────────────────────────

class TestEmit:
    @pytest.mark.asyncio
    async def test_calls_registered_handler(self):
        called = []
        ext = make_ext()
        ext.handlers['foo'] = [lambda e, ctx: called.append(e)]
        rt = make_runtime(ext)
        await rt.emit('foo', {'data': 1})
        assert called == [{'data': 1}]

    @pytest.mark.asyncio
    async def test_awaits_async_handler(self):
        called = []
        ext = make_ext()
        async def handler(e, ctx):
            called.append('async')
        ext.handlers['bar'] = [handler]
        rt = make_runtime(ext)
        await rt.emit('bar', {})
        assert called == ['async']

    @pytest.mark.asyncio
    async def test_returns_non_none_results(self):
        ext = make_ext()
        ext.handlers['ev'] = [lambda e, ctx: 'result_a', lambda e, ctx: None, lambda e, ctx: 'result_b']
        rt = make_runtime(ext)
        results = await rt.emit('ev', {})
        assert results == ['result_a', 'result_b']

    @pytest.mark.asyncio
    async def test_no_handlers_returns_empty(self):
        rt = make_runtime(make_ext())
        results = await rt.emit('unknown_event', {})
        assert results == []

    @pytest.mark.asyncio
    async def test_handler_exception_recorded(self):
        ext = make_ext("crash.py")
        def bad(e, ctx): raise ValueError("oops")
        ext.handlers['ev'] = [bad]
        rt = make_runtime(ext)
        results = await rt.emit('ev', {})
        assert results == []
        assert len(rt.errors) == 1
        assert rt.errors[0].extension_path == "crash.py"

    @pytest.mark.asyncio
    async def test_multiple_extensions_all_called(self):
        called = []
        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.handlers['ev'] = [lambda e, ctx: called.append('e1')]
        ext2.handlers['ev'] = [lambda e, ctx: called.append('e2')]
        rt = make_runtime(ext1, ext2)
        await rt.emit('ev', {})
        assert 'e1' in called and 'e2' in called


# ── emit_parallel ─────────────────────────────────────────────────────────────

class TestEmitParallel:
    @pytest.mark.asyncio
    async def test_all_handlers_called(self):
        called = []
        ext = make_ext()
        ext.handlers['p'] = [
            lambda e, ctx: called.append(1),
            lambda e, ctx: called.append(2),
        ]
        rt = make_runtime(ext)
        await rt.emit_parallel('p', {})
        assert set(called) == {1, 2}

    @pytest.mark.asyncio
    async def test_exception_does_not_crash(self):
        ext = make_ext("p.py")
        async def bad(e, ctx): raise RuntimeError("parallel fail")
        ext.handlers['pev'] = [bad]
        rt = make_runtime(ext)
        results = await rt.emit_parallel('pev', {})
        assert len(rt.errors) == 1


# ── has_handlers ─────────────────────────────────────────────────────────────

class TestHasHandlers:
    def test_true_when_handler_registered(self):
        ext = make_ext()
        ext.handlers['x'] = [lambda e, ctx: None]
        rt = make_runtime(ext)
        assert rt.has_handlers('x')

    def test_false_when_no_handlers(self):
        rt = make_runtime(make_ext())
        assert not rt.has_handlers('x')


# ── get_tools / get_commands ──────────────────────────────────────────────────

class TestGetProviderRegistrations:
    def test_get_providers_collects_from_all_extensions(self):
        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.inference_providers = [object()]
        ext2.inference_providers = [object(), object()]

        rt = make_runtime(ext1, ext2)
        assert len(rt.get_providers()) == 3

    def test_get_providers_empty_when_none_registered(self):
        rt = make_runtime(make_ext(), make_ext("e2.py"))
        assert rt.get_providers() == []

    def test_get_llm_apis_merges_last_writer_wins(self):
        class APIFirst: pass
        class APISecond: pass

        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.inference_apis = {'my_api': APIFirst}
        ext2.inference_apis = {'my_api': APISecond, 'other_api': APIFirst}

        rt = make_runtime(ext1, ext2)
        apis = rt.get_llm_apis()
        assert apis['my_api'] is APISecond
        assert 'other_api' in apis

    def test_get_llm_apis_empty_when_none_registered(self):
        rt = make_runtime(make_ext())
        assert rt.get_llm_apis() == {}

    def test_get_memory_providers_collects_from_all_extensions(self):
        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.memory_providers = [object(), object()]
        ext2.memory_providers = [object()]

        rt = make_runtime(ext1, ext2)
        assert len(rt.get_memory_providers()) == 3

    def test_get_memory_providers_empty_when_none_registered(self):
        rt = make_runtime(make_ext())
        assert rt.get_memory_providers() == []

    def test_get_memory_apis_merges_last_writer_wins(self):
        class Impl1: pass
        class Impl2: pass

        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.memory_apis = {'sqlite': Impl1}
        ext2.memory_apis = {'sqlite': Impl2, 'redis': Impl1}

        rt = make_runtime(ext1, ext2)
        apis = rt.get_memory_apis()
        assert apis['sqlite'] is Impl2
        assert 'redis' in apis

    def test_get_memory_apis_empty_when_none_registered(self):
        rt = make_runtime(make_ext())
        assert rt.get_memory_apis() == {}

    def test_provider_order_preserved_across_extensions(self):
        sentinel1 = object()
        sentinel2 = object()
        sentinel3 = object()

        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.inference_providers = [sentinel1, sentinel2]
        ext2.inference_providers = [sentinel3]

        rt = make_runtime(ext1, ext2)
        providers = rt.get_providers()
        assert providers == [sentinel1, sentinel2, sentinel3]


class TestGetToolsAndCommands:
    def test_get_tools_merges_all_extensions(self):
        from pydantic import BaseModel
        class P(BaseModel): pass
        async def exec(inv, **kw): return ToolResult.ok(inv.id, "ok")

        si = SourceInfo(path="t.py", source='local')
        td = ToolDefinition(name='t1', description='x', parameters=P, execute=exec)
        rt_tool = RegisteredTool(definition=td, source_info=si)

        ext = make_ext()
        ext.tools['t1'] = rt_tool
        rt = make_runtime(ext)
        tools = rt.get_tools()
        assert 't1' in tools

    def test_get_commands_merges_all_extensions(self):
        si = SourceInfo(path="c.py", source='local')
        async def h(reg, args): pass
        cmd = RegisteredCommand(name='do', source_info=si, description='test', handler=h)

        ext = make_ext()
        ext.commands['do'] = cmd
        rt = make_runtime(ext)
        cmds = rt.get_commands()
        assert 'do' in cmds

    def test_get_subagent_profiles_merges_all_extensions(self):
        from program.subagent.profile import SubagentProfile
        from pathlib import Path

        def _profile(name):
            return SubagentProfile(
                name=name, description=f'{name} agent',
                tools=[], system_prompt='', file_path=Path('x.py'),
            )

        ext1 = make_ext("e1.py")
        ext2 = make_ext("e2.py")
        ext1.subagent_profiles = [_profile('alpha')]
        ext2.subagent_profiles = [_profile('beta'), _profile('gamma')]

        rt = make_runtime(ext1, ext2)
        profiles = rt.get_subagent_profiles()
        names = [p.name for p in profiles]
        assert names == ['alpha', 'beta', 'gamma']

    def test_get_subagent_profiles_empty_when_none_registered(self):
        rt = make_runtime(make_ext(), make_ext("e2.py"))
        assert rt.get_subagent_profiles() == []

    def test_later_extension_wins_on_name_collision(self):
        from pydantic import BaseModel
        class P(BaseModel): pass
        async def exec(inv, **kw): return ToolResult.ok(inv.id, "ok")

        si = SourceInfo(path="t.py", source='local')
        td1 = ToolDefinition(name='t', description='first', parameters=P, execute=exec)
        td2 = ToolDefinition(name='t', description='second', parameters=P, execute=exec)
        rt1 = RegisteredTool(definition=td1, source_info=si)
        rt2 = RegisteredTool(definition=td2, source_info=si)

        ext1 = make_ext("a.py")
        ext2 = make_ext("b.py")
        ext1.tools['t'] = rt1
        ext2.tools['t'] = rt2

        runtime = make_runtime(ext1, ext2)
        tools = runtime.get_tools()
        assert tools['t'].definition.description == 'second'
