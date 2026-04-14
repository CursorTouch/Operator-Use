"""Tests for Hooks — register, emit, decorator, exception handling."""

import pytest
from operator_use.agent.hooks.service import Hooks
from operator_use.agent.hooks.events import (
    HookEvent,
    BeforeAgentStartContext,
    BeforeToolCallContext,
)


# --- Basic register / emit ---


@pytest.mark.asyncio
async def test_emit_calls_handler():
    hooks = Hooks()
    called = []

    async def handler(ctx):
        called.append(ctx)

    hooks.register(HookEvent.BEFORE_AGENT_START, handler)
    ctx = BeforeAgentStartContext(message=None, session=None)
    await hooks.emit(HookEvent.BEFORE_AGENT_START, ctx)
    assert len(called) == 1
    assert called[0] is ctx


@pytest.mark.asyncio
async def test_emit_returns_context():
    hooks = Hooks()
    ctx = BeforeAgentStartContext(message=None, session=None)
    returned = await hooks.emit(HookEvent.BEFORE_AGENT_START, ctx)
    assert returned is ctx


@pytest.mark.asyncio
async def test_emit_no_handlers_returns_context():
    hooks = Hooks()
    ctx = BeforeAgentStartContext(message=None, session=None)
    result = await hooks.emit(HookEvent.BEFORE_AGENT_START, ctx)
    assert result is ctx


@pytest.mark.asyncio
async def test_emit_multiple_handlers_called_in_order():
    hooks = Hooks()
    order = []

    async def h1(ctx):
        order.append(1)

    async def h2(ctx):
        order.append(2)

    async def h3(ctx):
        order.append(3)

    hooks.register(HookEvent.BEFORE_AGENT_START, h1)
    hooks.register(HookEvent.BEFORE_AGENT_START, h2)
    hooks.register(HookEvent.BEFORE_AGENT_START, h3)

    await hooks.emit(
        HookEvent.BEFORE_AGENT_START, BeforeAgentStartContext(message=None, session=None)
    )
    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_emit_handler_can_mutate_context():
    hooks = Hooks()

    async def mutate(ctx):
        ctx.skip = True

    hooks.register(HookEvent.BEFORE_TOOL_CALL, mutate)
    ctx = BeforeToolCallContext(session=None, tool_call=None)
    assert ctx.skip is False
    await hooks.emit(HookEvent.BEFORE_TOOL_CALL, ctx)
    assert ctx.skip is True


@pytest.mark.asyncio
async def test_emit_different_events_isolated():
    hooks = Hooks()
    called = []

    async def handler(ctx):
        called.append("start")

    hooks.register(HookEvent.BEFORE_AGENT_START, handler)
    await hooks.emit(HookEvent.AFTER_AGENT_END, BeforeAgentStartContext(message=None, session=None))
    assert called == []


# --- Unregister ---


@pytest.mark.asyncio
async def test_unregister_handler():
    hooks = Hooks()
    called = []

    async def handler(ctx):
        called.append(1)

    hooks.register(HookEvent.BEFORE_AGENT_START, handler)
    hooks.unregister(HookEvent.BEFORE_AGENT_START, handler)
    await hooks.emit(
        HookEvent.BEFORE_AGENT_START, BeforeAgentStartContext(message=None, session=None)
    )
    assert called == []


def test_unregister_nonexistent_no_error():
    hooks = Hooks()

    async def handler(ctx):
        pass

    hooks.unregister(HookEvent.BEFORE_AGENT_START, handler)  # Should not raise


# --- @on decorator ---


@pytest.mark.asyncio
async def test_on_decorator():
    hooks = Hooks()
    called = []

    @hooks.on(HookEvent.BEFORE_AGENT_START)
    async def handler(ctx):
        called.append(True)

    await hooks.emit(
        HookEvent.BEFORE_AGENT_START, BeforeAgentStartContext(message=None, session=None)
    )
    assert called == [True]


@pytest.mark.asyncio
async def test_on_decorator_returns_function():
    hooks = Hooks()

    @hooks.on(HookEvent.BEFORE_AGENT_START)
    async def handler(ctx):
        pass

    assert callable(handler)


# --- Exception handling ---


@pytest.mark.asyncio
async def test_emit_handler_exception_does_not_abort():
    hooks = Hooks()
    called = []

    async def bad_handler(ctx):
        raise RuntimeError("intentional error")

    async def good_handler(ctx):
        called.append(True)

    hooks.register(HookEvent.BEFORE_AGENT_START, bad_handler)
    hooks.register(HookEvent.BEFORE_AGENT_START, good_handler)

    ctx = BeforeAgentStartContext(message=None, session=None)
    await hooks.emit(HookEvent.BEFORE_AGENT_START, ctx)
    # good_handler must still run despite bad_handler raising
    assert called == [True]


@pytest.mark.asyncio
async def test_emit_all_exceptions_logged_but_context_returned():
    hooks = Hooks()

    async def always_raise(ctx):
        raise ValueError("boom")

    hooks.register(HookEvent.BEFORE_AGENT_START, always_raise)
    ctx = BeforeAgentStartContext(message=None, session=None)
    result = await hooks.emit(HookEvent.BEFORE_AGENT_START, ctx)
    assert result is ctx


# --- HookEvent enum ---


def test_hook_event_values():
    assert HookEvent.BEFORE_AGENT_START == "before_agent_start"
    assert HookEvent.AFTER_AGENT_END == "after_agent_end"
    assert HookEvent.BEFORE_TOOL_CALL == "before_tool_call"
    assert HookEvent.AFTER_TOOL_CALL == "after_tool_call"
    assert HookEvent.BEFORE_LLM_CALL == "before_llm_call"
    assert HookEvent.AFTER_LLM_CALL == "after_llm_call"
