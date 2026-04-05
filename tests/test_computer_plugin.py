"""Tests for ComputerPlugin — tool registration, prompt injection, hook wiring.

Desktop and WatchDog initialisation (which requires platform accessibility
frameworks) is avoided by creating plugins with enabled=False and manually
setting _enabled=True where needed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_use.computer.plugin import ComputerPlugin, SYSTEM_PROMPT
from operator_use.agent.hooks.service import Hooks
from operator_use.agent.hooks.events import HookEvent


# ---------------------------------------------------------------------------
# get_system_prompt — gated on _enabled
# ---------------------------------------------------------------------------

def test_disabled_plugin_has_no_prompt():
    assert ComputerPlugin(enabled=False).get_system_prompt() is None


def test_enabled_plugin_returns_system_prompt():
    plugin = ComputerPlugin(enabled=False)
    plugin._enabled = True
    prompt = plugin.get_system_prompt()
    assert prompt is SYSTEM_PROMPT
    assert "desktop" in prompt.lower()
    # Prompt is plain Markdown — assert on actual content, not old XML tags
    assert "computer_task" in prompt


# ---------------------------------------------------------------------------
# register_hooks — hooks NOT registered to main agent (subagent arch)
# ---------------------------------------------------------------------------

def test_disabled_plugin_registers_no_hooks():
    plugin = ComputerPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]


def test_enabled_plugin_does_not_register_hooks_to_main_agent():
    """Hooks are intentionally not wired to main agent — subagent manages its own state."""
    plugin = ComputerPlugin(enabled=False)
    plugin._enabled = True
    hooks = Hooks()
    plugin.register_hooks(hooks)
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]


def test_unregister_hooks_is_safe_noop():
    plugin = ComputerPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    plugin.unregister_hooks(hooks)  # must not raise
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]


# ---------------------------------------------------------------------------
# attach_prompt — gated on _enabled
# ---------------------------------------------------------------------------

def test_disabled_plugin_does_not_inject_prompt():
    plugin = ComputerPlugin(enabled=False)
    context = MagicMock()
    plugin.attach_prompt(context)
    context.register_plugin_prompt.assert_not_called()
    assert plugin._context is context


def test_enabled_plugin_injects_prompt():
    plugin = ComputerPlugin(enabled=False)
    plugin._enabled = True
    context = MagicMock()
    plugin.attach_prompt(context)
    context.register_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


def test_detach_prompt_removes_injected_prompt():
    plugin = ComputerPlugin(enabled=False)
    plugin._enabled = True
    context = MagicMock()
    plugin.attach_prompt(context)
    plugin.detach_prompt(context)
    context.unregister_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# enable() / disable() — full lifecycle (no Desktop/WatchDog init)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enable_injects_prompt_no_hooks():
    """enable() registers tools and injects prompt — hooks NOT wired to main agent."""
    plugin = ComputerPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.enable()

    assert plugin._enabled is True
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]
    context.register_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_disable_removes_prompt():
    plugin = ComputerPlugin(enabled=False)
    plugin._enabled = True
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.disable()

    assert plugin._enabled is False
    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]
    context.unregister_plugin_prompt.assert_called_once_with(SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_enable_then_disable_leaves_no_hooks():
    plugin = ComputerPlugin(enabled=False)
    hooks = Hooks()
    plugin.register_hooks(hooks)
    context = MagicMock()
    plugin.attach_prompt(context)

    await plugin.enable()
    await plugin.disable()

    assert plugin._state_hook not in hooks._handlers[HookEvent.BEFORE_LLM_CALL]
    assert plugin._wait_for_ui_hook not in hooks._handlers[HookEvent.AFTER_TOOL_CALL]


# ---------------------------------------------------------------------------
# _state_hook — gracefully handles desktop.get_state failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_hook_appends_desktop_state():
    plugin = ComputerPlugin(enabled=False)
    mock_state = MagicMock()
    mock_state.to_string.return_value = "Active: Notepad | Elements: [button 'Save']"
    plugin.desktop = MagicMock()

    from unittest.mock import patch
    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_state)
        ctx = MagicMock()
        ctx.messages = []
        await plugin._state_hook(ctx)

    assert len(ctx.messages) == 1
    assert "Notepad" in ctx.messages[0].content


@pytest.mark.asyncio
async def test_state_hook_handles_exception_gracefully():
    plugin = ComputerPlugin(enabled=False)
    plugin.desktop = MagicMock()

    from unittest.mock import patch
    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=RuntimeError("accessibility error"))
        ctx = MagicMock()
        ctx.messages = []
        result = await plugin._state_hook(ctx)

    assert result is ctx
    assert ctx.messages == []


# ---------------------------------------------------------------------------
# _wait_for_ui_hook — watchdog not set → sleeps briefly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wait_for_ui_hook_no_watchdog_sleeps():
    plugin = ComputerPlugin(enabled=False)
    plugin.watchdog = None

    ctx = MagicMock()
    result = await plugin._wait_for_ui_hook(ctx)
    assert result is ctx
