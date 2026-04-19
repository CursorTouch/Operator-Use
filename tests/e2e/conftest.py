"""E2E test fixtures.

All fixtures are designed to run without real LLM API keys so the suite is
CI-safe. The ``mock_llm_provider`` fixture is the cornerstone: it returns a
MagicMock that satisfies the BaseChatLLM protocol and produces scripted,
deterministic responses.

Fixture hierarchy
-----------------
mock_llm_provider          — base mock LLM, always returns "pong"
test_agent                 — Agent wired to mock_llm_provider
test_gateway               — Gateway with a stub channel (message injection)
test_agent_with_echo_tool  — Agent + registered echo tool for tool-call tests
mock_llm_with_tool_call    — async factory that exercises the tool-call path
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from unittest.mock import AsyncMock, MagicMock

from operator_use.agent.service import Agent
from operator_use.bus.service import Bus
from operator_use.bus.views import OutgoingMessage
from operator_use.gateway.channels.base import BaseChannel
from operator_use.gateway.service import Gateway
from operator_use.messages.service import HumanMessage
from operator_use.orchestrator.service import Orchestrator
from operator_use.providers.events import LLMEvent, LLMEventType, ToolCall
from operator_use.agent.tools.service import Tool


# ---------------------------------------------------------------------------
# Stub channel — captures outbound messages for gateway-level tests
# ---------------------------------------------------------------------------

class _StubChannel(BaseChannel):
    """Minimal channel that records sent messages and does nothing else."""

    def __init__(self, bus: Bus) -> None:
        config = MagicMock()
        config.account_id = ""
        super().__init__(config=config, bus=bus)
        self.sent: list[OutgoingMessage] = []

    @property
    def name(self) -> str:
        return "stub"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.running = False

    async def _listen(self) -> None:
        pass

    async def send(self, message: OutgoingMessage) -> int | None:
        self.sent.append(message)
        return 1


# ---------------------------------------------------------------------------
# Echo tool — trivial tool used by tool-call tests
# ---------------------------------------------------------------------------

class _EchoParams(BaseModel):
    message: str


class _EchoTool(Tool):
    """Returns the input message verbatim. Used to verify tool execution."""

    def __init__(self) -> None:
        super().__init__(name="echo", description="Echo the input message.", model=_EchoParams)

    def __call__(self, fn):  # decorator-style registration
        self.function = fn
        return self

    def run(self, message: str, **kwargs) -> str:  # type: ignore[override]
        return message


def _make_echo_tool() -> _EchoTool:
    tool = _EchoTool()

    def _fn(message: str, **kwargs):
        return message

    tool.function = _fn
    return tool


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm_provider():
    """Mock LLM that always returns 'pong' — no API keys required.

    Satisfies the BaseChatLLM protocol via MagicMock with an AsyncMock
    ``ainvoke`` that returns a deterministic LLMEvent(TEXT, content="pong").
    """
    llm = MagicMock()
    llm.model_name = "mock-llm-e2e"
    llm.provider = "mock"
    llm.astream = None  # force the non-streaming path in Agent._loop

    pong_event = LLMEvent(type=LLMEventType.TEXT, content="pong")
    llm.ainvoke = AsyncMock(return_value=pong_event)
    return llm


@pytest.fixture()
def test_agent(mock_llm_provider, tmp_path):
    """Fully-configured Agent with mock LLM and a temp workspace.

    - No channels, no bus, no cron — pure agent loop.
    - ``tools=[]`` avoids loading built-in tools that require external deps.
    - Workspace is isolated per test via pytest's ``tmp_path``.
    """
    return Agent(
        llm=mock_llm_provider,
        agent_id="e2e-agent",
        workspace=tmp_path,
        tools=[],
        max_iterations=10,
    )


@pytest.fixture()
def test_gateway():
    """Gateway with an in-memory stub channel for integration-level tests.

    Returns a tuple (gateway, stub_channel, bus) so tests can inject messages
    and inspect outbound traffic without starting a real server.
    """
    bus = Bus()
    channel = _StubChannel(bus=bus)
    gateway = Gateway(bus=bus)
    gateway.add_channel(channel)
    return gateway, channel, bus


@pytest.fixture()
def test_agent_with_echo_tool(mock_llm_provider, tmp_path):
    """Agent pre-loaded with the echo tool for tool-call pipeline tests."""
    agent = Agent(
        llm=mock_llm_provider,
        agent_id="e2e-echo-agent",
        workspace=tmp_path,
        tools=[],
        max_iterations=10,
    )
    agent.tool_register.register(_make_echo_tool())
    return agent


@pytest.fixture()
def mock_llm_with_tool_call(tmp_path):
    """Factory fixture: configure an agent's LLM to emit a tool_call then TEXT.

    Usage::

        tool_calls = await mock_llm_with_tool_call(agent, "run the echo tool")
        assert_tool_called(tool_calls, "echo")

    Returns an async callable that:
    1. Replaces the agent's ``llm.ainvoke`` with a side_effect that first
       emits a ToolCall for "echo" then a TEXT event.
    2. Runs the agent and records which tool names were invoked.
    3. Returns the list of recorded tool names.
    """

    async def _factory(agent: Agent, text: str, session_id: str = "e2e:tool-call") -> list[str]:
        tool_event = LLMEvent(
            type=LLMEventType.TOOL_CALL,
            tool_call=ToolCall(id="e2e-t1", name="echo", params={"message": "hello from e2e"}),
        )
        done_event = LLMEvent(type=LLMEventType.TEXT, content="tool run complete")
        agent.llm.ainvoke = AsyncMock(side_effect=[tool_event, done_event])

        called_tools: list[str] = []
        original_aexecute = agent.tool_register.aexecute

        async def _recording_aexecute(name: str, params: dict) -> object:
            called_tools.append(name)
            return await original_aexecute(name, params)

        agent.tool_register.aexecute = _recording_aexecute  # type: ignore[method-assign]

        await agent.run(message=HumanMessage(content=text), session_id=session_id)
        return called_tools

    return _factory


@pytest.fixture()
def test_orchestrator(mock_llm_provider, tmp_path):
    """Orchestrator wired to a test agent and an in-memory Bus.

    The Orchestrator is the highest-level pipeline coordinator in this codebase:
    it owns STT/TTS, message building (IncomingMessage → HumanMessage), agent
    routing, and the outgoing-message construction.  There is no separate
    "Gateway" involved here because the fixture targets the Orchestrator layer
    directly, not the full channel-gateway stack.

    ``process_direct()`` is used in tests instead of ``ainvoke()`` so the test
    can invoke the pipeline synchronously without starting the async consume
    loop that blocks indefinitely on the bus queue.

    Components wired together:
    - mock_llm_provider  — deterministic LLM; no API keys required
    - Agent              — LLM agentic loop; isolated tmp workspace
    - Bus                — in-memory async queues
    - Orchestrator       — routes Bus messages to the Agent and back

    Returns an Orchestrator instance ready for use with ``process_direct()``.
    """
    bus = Bus()
    agent = Agent(
        llm=mock_llm_provider,
        agent_id="e2e-orchestrator-agent",
        workspace=tmp_path,
        tools=[],
        max_iterations=10,
        bus=bus,
    )
    return Orchestrator(
        bus=bus,
        agents={"operator": agent},
        default_agent="operator",
        streaming=False,  # disable streaming so tests use the simple ainvoke path
    )
