from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.helpers import make_agent
from program.bus.service import Bus
from program.gateway.service import Gateway, _SessionEntry
from program.inference.types import LLMContext, StartEvent
from program.settings.manager import SettingsManager


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
        self.current_session = agent
        self._context = SimpleNamespace(settings_manager=SettingsManager.in_memory())

    def create_session_agent(self):
        return self.current_session


@pytest.mark.asyncio
async def test_gateway_stop_cancels_inflight_agent_session() -> None:
    llm = BlockingLLM()
    agent, _ = make_agent(llm)
    gateway = Gateway(Bus(), _Runtime(agent))

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
