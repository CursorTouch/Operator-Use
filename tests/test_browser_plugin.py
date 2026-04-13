"""Tests for BrowserPlugin — tool registration, prompt injection, hook wiring."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_use.web.plugin import BrowserPlugin, SYSTEM_PROMPT
from operator_use.agent.tools.registry import ToolRegistry
from operator_use.agent.hooks.service import Hooks
from operator_use.agent.hooks.events import HookEvent


# ---------------------------------------------------------------------------
# get_system_prompt — gated on _enabled
# ---------------------------------------------------------------------------


def test_disabled_plugin_has_no_prompt():
    assert BrowserPlugin(enabled=False).get_system_prompt() is None


def test_enabled_plugin_returns_system_prompt():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    prompt = plugin.get_system_prompt()
    assert prompt is SYSTEM_PROMPT
    assert "browser" in prompt.lower()
    assert "<perception>" in prompt
    assert "<tool_use>" in prompt
    assert "<execution_principles>" in prompt


# ---------------------------------------------------------------------------
# register_tools — only when enabled
# ---------------------------------------------------------------------------


def test_disabled_plugin_registers_no_tools():
    plugin = BrowserPlugin(enabled=False)
    registry = ToolRegistry()
    plugin.register_tools(registry)
    assert registry.get("browser") is None


def test_enabled_plugin_registers_browser_tool():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    plugin.browser = MagicMock()
    registry = ToolRegistry()
    plugin.register_tools(registry)
    assert registry.get("browser") is not None


def test_unregister_tools_removes_browser_tool():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    plugin.browser = MagicMock()
    registry = ToolRegistry()
    plugin.register_tools(registry)
    plugin.unregister_tools(registry)
    assert registry.get("browser") is None


# ---------------------------------------------------------------------------
# register_hooks — BEFORE_LLM_CALL gated on _enabled
# ---------------------------------------------------------------------------


def test_disabled_plugin_registers_no_hooks():
    plugin = BrowserPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]


def test_enabled_plugin_registers_state_hook():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    hooks = Hooks()
    plugin.register_hooks(hooks)
    assert plugin._state_hook in hooks._handlers[HookEvent.BEFORE_LLM_CALL]


def test_unregister_hooks_removes_state_hook():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    hooks = Hooks()
    plugin.register_hooks(hooks)
    plugin.unregister_hooks(hooks)
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]


# ---------------------------------------------------------------------------
# attach_prompt — gated on _enabled, stores context reference
# ---------------------------------------------------------------------------


def test_disabled_plugin_does_not_inject_prompt():
    plugin = BrowserPlugin(enabled=False)
    context = MagicMock()
    plugin.attach_prompt(context)
    context.register_plugin_prompt.assert_not_called()
    assert plugin._context is context  # reference still stored


def test_enabled_plugin_injects_prompt():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    context = MagicMock()
    plugin.attach_prompt(context)
    context.register_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


def test_detach_prompt_removes_injected_prompt():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    context = MagicMock()
    plugin.attach_prompt(context)
    plugin.detach_prompt(context)
    context.unregister_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# enable() / disable() — full lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_registers_hooks_and_injects_prompt():
    plugin = BrowserPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.enable()

    assert plugin._enabled is True
    assert plugin._state_hook in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    context.register_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_disable_unregisters_hooks_and_removes_prompt():
    plugin = BrowserPlugin(enabled=False)
    plugin._enabled = True
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.disable()

    assert plugin._enabled is False
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    context.unregister_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_enable_then_disable_leaves_no_hooks():
    plugin = BrowserPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.enable()
    await plugin.disable()

    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]


# ---------------------------------------------------------------------------
# _state_hook — gracefully skips when browser has no active session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_hook_skips_when_no_browser_client():
    plugin = BrowserPlugin(enabled=False)
    plugin.browser = MagicMock()
    plugin.browser._client = None  # no active session

    ctx = MagicMock()
    ctx.messages = []
    result = await plugin._state_hook(ctx)

    assert result is ctx
    assert ctx.messages == []


@pytest.mark.asyncio
async def test_state_hook_appends_state_message():
    plugin = BrowserPlugin(enabled=False)
    plugin.browser = MagicMock()
    plugin.browser._client = MagicMock()
    plugin.browser._get_current_session_id = MagicMock(return_value="session-1")

    mock_state = MagicMock()
    mock_state.to_string.return_value = "URL: https://example.com\nElements: [button 'Click me']"
    plugin.browser.get_state = AsyncMock(return_value=mock_state)

    ctx = MagicMock()
    ctx.messages = []
    await plugin._state_hook(ctx)

    assert len(ctx.messages) == 1
    assert "https://example.com" in ctx.messages[0].content


@pytest.mark.asyncio
async def test_state_hook_skips_when_state_unavailable():
    plugin = BrowserPlugin(enabled=False)
    plugin.browser = MagicMock()
    plugin.browser._client = MagicMock()
    plugin.browser._get_current_session_id = MagicMock(return_value="session-1")
    plugin.browser.get_state = AsyncMock(return_value=None)

    ctx = MagicMock()
    ctx.messages = []
    await plugin._state_hook(ctx)

    plugin.browser.get_state.assert_awaited_once()
    assert ctx.messages == []
