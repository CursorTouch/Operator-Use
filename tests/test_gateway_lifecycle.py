from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.helpers import make_agent
from operator_use.bus.service import Bus
from operator_use.gateway.service import Gateway, _SessionEntry
from operator_use.inference.types import LLMContext, StartEvent
from operator_use.settings.manager import SettingsManager


class BlockingLLM:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.model = SimpleNamespace(name="blocking", provider="fake")
        self.api = SimpleNamespace(options=SimpleNamespace())

    async def stream(self, context: LLMContext):
        self.started.set()
        yield StartEvent()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


class _Runtime:
    def __init__(self, agent) -> None:
        self.bus = Bus()
        self.current_session = agent
        self._context = SimpleNamespace(settings_manager=SettingsManager.in_memory())
        self.settings_manager = self._context.settings_manager
        self.auth_manager = None
        self.unified_session_enabled = True
        self.created_sessions = 0

    def create_session_agent(self):
        self.created_sessions += 1
        return self.current_session


@pytest.mark.asyncio
async def test_gateway_stop_cancels_inflight_agent_session() -> None:
    llm = BlockingLLM()
    agent, _ = make_agent(llm)
    gateway = Gateway(_Runtime(agent))

    task = asyncio.create_task(
        gateway._run_session(
            "telegram:chat-1",
            "telegram",
            "chat-1",
            agent,
            "hello",
        )
    )
    gateway._sessions["telegram:chat-1"] = _SessionEntry(agent=agent, task=task)

    await asyncio.wait_for(llm.started.wait(), timeout=1)
    assert not agent.is_idle()

    await gateway.stop()

    assert task.done()
    assert llm.cancelled.is_set()
    assert agent.is_idle()


def test_gateway_uses_runtime_bus() -> None:
    agent, _ = make_agent(BlockingLLM())
    runtime = _Runtime(agent)
    gateway = Gateway(runtime)

    assert gateway._bus is runtime.bus


def test_gateway_uses_runtime_session_policy() -> None:
    agent, _ = make_agent(BlockingLLM())
    runtime = _Runtime(agent)
    gateway = Gateway(runtime)

    assert gateway._get_or_create_session("telegram:shared").agent is agent
    assert runtime.created_sessions == 0

    runtime.unified_session_enabled = False
    assert gateway._get_or_create_session("telegram:isolated").agent is agent
    assert runtime.created_sessions == 1
