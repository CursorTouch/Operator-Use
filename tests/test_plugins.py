"""Tests for Plugin base class — tools, prompts, hooks registration."""

import pytest
from unittest.mock import MagicMock

from operator_use.plugins.base import Plugin
from operator_use.agent.tools.registry import ToolRegistry
from operator_use.agent.hooks.service import Hooks
from operator_use.agent.hooks.events import HookEvent
from operator_use.agent.tools.service import Tool
from pydantic import BaseModel


# --- Concrete plugin stubs ---


class EmptyPlugin(Plugin):
    name = "empty"


class PromptPlugin(Plugin):
    name = "prompt_plugin"

    def get_system_prompt(self) -> str:
        return "## Extra Instructions\nDo this."


class DummyParams(BaseModel):
    value: str


class DummyTool(Tool):
    def __init__(self):
        super().__init__(name="dummy_tool", description="A dummy tool", model=DummyParams)

    def __call__(self, fn):
        self.function = fn
        return self


dummy_tool = DummyTool()


@dummy_tool
def _dummy(value: str, **kwargs):
    return f"result:{value}"


class ToolPlugin(Plugin):
    name = "tool_plugin"

    def get_tools(self):
        return [dummy_tool]


# --- get_tools ---


def test_empty_plugin_returns_no_tools():
    assert EmptyPlugin().get_tools() == []


def test_tool_plugin_returns_tools():
    tools = ToolPlugin().get_tools()
    assert any(t.name == "dummy_tool" for t in tools)


# --- get_system_prompt ---


def test_empty_plugin_no_prompt():
    assert EmptyPlugin().get_system_prompt() is None


def test_prompt_plugin_returns_prompt():
    p = PromptPlugin()
    prompt = p.get_system_prompt()
    assert "Extra Instructions" in prompt


# --- register_tools / unregister_tools ---


def test_register_tools_adds_to_registry():
    registry = ToolRegistry()
    ToolPlugin().register_tools(registry)
    assert registry.get("dummy_tool") is not None


def test_unregister_tools_removes_from_registry():
    registry = ToolRegistry()
    ToolPlugin().register_tools(registry)
    ToolPlugin().unregister_tools(registry)
    assert registry.get("dummy_tool") is None


def test_empty_plugin_register_tools_no_error():
    registry = ToolRegistry()
    EmptyPlugin().register_tools(registry)  # should not raise
    assert registry.list_tools() == []


# --- attach_prompt / detach_prompt ---


def test_attach_prompt_calls_context():
    context = MagicMock()
    PromptPlugin().attach_prompt(context)
    context.register_plugin_prompt.assert_called_once()


def test_attach_prompt_empty_plugin_skips():
    context = MagicMock()
    EmptyPlugin().attach_prompt(context)
    context.register_plugin_prompt.assert_not_called()


def test_detach_prompt_calls_context():
    context = MagicMock()
    PromptPlugin().detach_prompt(context)
    context.unregister_plugin_prompt.assert_called_once()


def test_detach_prompt_empty_plugin_skips():
    context = MagicMock()
    EmptyPlugin().detach_prompt(context)
    context.unregister_plugin_prompt.assert_not_called()


# --- register_hooks / unregister_hooks ---


def test_register_hooks_default_no_op():
    hooks = Hooks()
    EmptyPlugin().register_hooks(hooks)  # should not raise


def test_unregister_hooks_default_no_op():
    hooks = Hooks()
    EmptyPlugin().unregister_hooks(hooks)  # should not raise


@pytest.mark.asyncio
async def test_plugin_hooks_fire_on_emit():
    class HookPlugin(Plugin):
        name = "hook_plugin"
        fired = []

        def register_hooks(self, hooks):
            @hooks.on(HookEvent.BEFORE_AGENT_START)
            async def on_start(ctx):
                HookPlugin.fired.append("start")

    hooks = Hooks()
    HookPlugin().register_hooks(hooks)

    from operator_use.agent.hooks.events import BeforeAgentStartContext

    await hooks.emit(
        HookEvent.BEFORE_AGENT_START, BeforeAgentStartContext(message=None, session=None)
    )
    assert "start" in HookPlugin.fired


# --- Plugin integrated with Agent ---


def test_plugin_registered_in_agent(tmp_path):
    from unittest.mock import MagicMock, AsyncMock
    from operator_use.agent.service import Agent
    from operator_use.providers.events import LLMEvent, LLMEventType

    llm = MagicMock()
    llm.model_name = "mock"
    llm.astream = None
    llm.ainvoke = AsyncMock(return_value=LLMEvent(type=LLMEventType.TEXT, content="ok"))

    plugin = ToolPlugin()
    agent = Agent(llm=llm, workspace=tmp_path, plugins=[plugin])

    assert agent.get_plugin("tool_plugin") is plugin
    assert agent.tool_register.get("dummy_tool") is not None
