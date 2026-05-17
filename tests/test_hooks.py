"""Hooks service — register, clear, subscribe, async handlers, exception isolation."""
from __future__ import annotations

import asyncio

import pytest

from program.hooks.service import Hooks
from program.hooks.types import (
    AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageEndEvent,
    SavePointEvent, SettledEvent,
)


class TestHooksRegisterAndClear:
    @pytest.mark.asyncio
    async def test_register_and_emit(self):
        hooks = Hooks()
        received = []
        hooks.register('agent_start', lambda e: received.append(e))
        await hooks.emit(AgentStartEvent())
        assert received

    @pytest.mark.asyncio
    async def test_clear_single_event(self):
        hooks = Hooks()
        received = []
        hooks.register('agent_start', lambda e: received.append(e))
        hooks.clear('agent_start')
        await hooks.emit(AgentStartEvent())
        assert not received

    @pytest.mark.asyncio
    async def test_clear_all(self):
        hooks = Hooks()
        received = []
        hooks.register('agent_start', lambda e: received.append("start"))
        hooks.register('agent_end', lambda e: received.append("end"))
        hooks.clear()
        await hooks.emit(AgentStartEvent())
        await hooks.emit(AgentEndEvent())
        assert not received

    def test_handler_count_accurate(self):
        hooks = Hooks()
        assert hooks.handler_count('ev') == 0
        hooks.register('ev', lambda e: None)
        hooks.register('ev', lambda e: None)
        assert hooks.handler_count('ev') == 2

    def test_registered_events_lists_active(self):
        hooks = Hooks()
        hooks.register('ev_a', lambda e: None)
        hooks.register('ev_b', lambda e: None)
        events = hooks.registered_events()
        assert 'ev_a' in events
        assert 'ev_b' in events


class TestHooksSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_receives_all_events(self):
        hooks = Hooks()
        received = []
        hooks.subscribe(lambda e: received.append(e))
        await hooks.emit(AgentStartEvent())
        await hooks.emit(AgentEndEvent())
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        hooks = Hooks()
        received = []
        unsub = hooks.subscribe(lambda e: received.append(e))
        await hooks.emit(AgentStartEvent())
        assert received
        unsub()
        received.clear()
        await hooks.emit(AgentStartEvent())
        assert not received


class TestHooksAsyncHandlers:
    @pytest.mark.asyncio
    async def test_async_handler_awaited(self):
        hooks = Hooks()
        results = []
        async def async_h(e):
            await asyncio.sleep(0)
            results.append("async_done")
        hooks.register('agent_start', async_h)
        await hooks.emit(AgentStartEvent())
        assert "async_done" in results

    @pytest.mark.asyncio
    async def test_sync_handler_called(self):
        hooks = Hooks()
        results = []
        hooks.register('agent_start', lambda e: results.append("sync"))
        await hooks.emit(AgentStartEvent())
        assert "sync" in results


class TestHooksExceptionIsolation:
    @pytest.mark.asyncio
    async def test_bad_handler_does_not_block_good_handler(self):
        hooks = Hooks()
        results = []
        hooks.register('agent_start', lambda e: (_ for _ in ()).throw(RuntimeError("crash")))
        hooks.register('agent_start', lambda e: results.append("good"))
        await hooks.emit(AgentStartEvent())
        assert "good" in results

    @pytest.mark.asyncio
    async def test_multiple_handlers_all_called(self):
        hooks = Hooks()
        log = []
        hooks.register('agent_start', lambda e: log.append("A"))
        hooks.register('agent_start', lambda e: log.append("B"))
        hooks.register('agent_start', lambda e: log.append("C"))
        await hooks.emit(AgentStartEvent())
        assert log == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_unregister_via_return_value(self):
        hooks = Hooks()
        results = []
        unregister = hooks.register('agent_start', lambda e: results.append("x"))
        await hooks.emit(AgentStartEvent())
        assert results
        unregister()
        results.clear()
        await hooks.emit(AgentStartEvent())
        assert not results
