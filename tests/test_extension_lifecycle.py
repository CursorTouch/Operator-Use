"""Extension lifecycle — context event, tool blocking, result modification, error isolation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from helpers import FakeLLM, make_tool, text_seq, tool_call_seq, AnyParams

from program.extension.types import (
    Extension, ContextEventResult, ToolCallEventResult, ToolResultEventResult,
    LoadExtensionsResult,
)
from program.message.types import UserMessage
from program.skill.types import SourceInfo
from program.tool.types import Tool, ToolKind, ToolResult


def _make_ext(handlers: dict) -> Extension:
    si = SourceInfo(path="ext.py", source="local")
    ext = Extension(path="ext.py", source_info=si)
    ext.handlers = handlers
    return ext


def _make_agent_with_ext(llm, ext, tools=None):
    from helpers import make_agent
    from program.hooks.service import Hooks
    from program.extension.runtime import ExtensionRuntime
    from pathlib import Path
    from program.agent.service import Agent
    from program.agent.types import AgentConfig
    from program.session.manager import SessionManager
    from program.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
    from program.compaction.strategy.types import CompactionSettings
    from program.resource.types import BaseResourceLoader

    class FakeLoader(BaseResourceLoader):
        def get_extensions(self): return LoadExtensionsResult()
        def get_skills(self): return [], []
        def get_tools(self): return []
        def get_commands(self): return []
        def get_hooks(self): return []
        def get_context_files(self): return []
        def get_system_prompt(self): return None
        def get_append_system_prompt(self): return []
        def extend_resources(self, p): pass
        def get_subagent_profiles(self): return []
        def get_diagnostics(self, runtime=None): return []
        async def reload(self): pass

    class _Null: pass

    h = Hooks()
    sm = SessionManager.in_memory()
    from program.engine.service import Engine
    engine = Engine(llm=llm, tools=tools or [], hooks=h)
    load_result = LoadExtensionsResult(extensions=[ext])
    config = AgentConfig(cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0)
    agent = Agent(
        engine=engine,
        session_manager=sm,
        resource_loader=FakeLoader(),
        extension_runtime=ExtensionRuntime(load_result, _Null(), h),
        compaction=Compaction(llm=llm, settings=CompactionSettings(enabled=False)),
        config=config,
    )
    agent._extensions = ExtensionRuntime(load_result, agent, h)
    return agent, sm


class TestContextEvent:
    @pytest.mark.asyncio
    async def test_context_event_replaces_messages(self):
        injected = [UserMessage.text("INJECTED")]
        ext = _make_ext({'context': [lambda e, ctx: ContextEventResult(messages=injected)]})

        captured = []

        class CapLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
            async def stream(self, context):
                captured.append(context.messages)
                for ev in text_seq("ok"):
                    yield ev

        agent, _ = _make_agent_with_ext(CapLLM(), ext)
        await agent.invoke("original")
        assert captured and any(
            any(getattr(c, 'content', '') == 'INJECTED' for c in m.contents)
            for m in captured[0]
        )


class TestToolCallBlocking:
    @pytest.mark.asyncio
    async def test_extension_blocks_tool_call(self):
        ext = _make_ext({'tool_call': [lambda e, ctx: ToolCallEventResult(block=True, reason="blocked")]})
        executed = []

        class Track(Tool):
            async def execute(self, invocation, **kwargs):
                executed.append(True)
                return ToolResult.ok(invocation.id, "should_not_run")

        tool = Track(name="guarded", description="x", schema=AnyParams, kind=ToolKind.Read)
        agent, _ = _make_agent_with_ext(
            FakeLLM(tool_call_seq("t1", "guarded"), text_seq("done")), ext, tools=[tool]
        )
        agent._engine._tools["guarded"] = tool
        await agent.invoke("use the guarded tool")
        assert not executed


class TestToolResultModification:
    @pytest.mark.asyncio
    async def test_extension_modifies_tool_result(self):
        ext = _make_ext({'tool_result': [lambda e, ctx: ToolResultEventResult(content="OVERRIDDEN")]})
        received_results = []

        class CapLLM:
            model = SimpleNamespace(name="fake", provider="fake")
            api = SimpleNamespace(options=SimpleNamespace())
            def __init__(self): self._calls = 0
            async def stream(self, context):
                self._calls += 1
                if self._calls == 1:
                    for ev in tool_call_seq("t1", "t"):
                        yield ev
                else:
                    # Capture what messages the LLM sees on the second call
                    for m in context.messages:
                        if hasattr(m, 'contents'):
                            for c in m.contents:
                                if hasattr(c, 'content') and c.content:
                                    received_results.append(c.content)
                    for ev in text_seq("done"):
                        yield ev

        # Do NOT nullify after_tool_call — that's what calls the extension
        agent, _ = _make_agent_with_ext(CapLLM(), ext, tools=[make_tool("t", "original")])
        await agent.invoke("use the tool")
        assert any("OVERRIDDEN" in str(r) for r in received_results)


class TestExtensionErrorIsolation:
    @pytest.mark.asyncio
    async def test_extension_handler_exception_does_not_crash_agent(self):
        ext = _make_ext({'context': [lambda e, ctx: (_ for _ in ()).throw(RuntimeError("ext crash"))]})
        agent, _ = _make_agent_with_ext(FakeLLM(text_seq("ok")), ext)
        # Should not raise — extension errors are caught
        await agent.invoke("go")
