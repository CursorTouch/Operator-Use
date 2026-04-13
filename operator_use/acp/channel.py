"""ACP channel — integrates the ACP server into the Operator gateway.

When enabled, this channel:
  1. Starts an ACP-compliant REST server that accepts runs from external ACP clients
     (e.g. Claude Code, Zed, JetBrains) and routes them to the correct Operator agent.
  2. Translates ACP RunCreateRequest → IncomingMessage (bus) and
     OutgoingMessage (bus) → ACP run output / SSE stream.
  3. Exposes all configured agents via GET /agents so remote machines can discover them.

Config example (multi-agent):
    from operator_use.acp.channel import ACPChannel
    from operator_use.acp.config import ACPServerConfig

    acp_channel = ACPChannel(
        config=ACPServerConfig(enabled=True, port=8765),
        bus=bus,
        agents=agents,   # dict[str, Agent] from the Orchestrator
    )
    gateway.add_channel(acp_channel)
"""

from __future__ import annotations

import asyncio
import logging

from operator_use.acp.config import ACPServerConfig
from operator_use.acp.models import AgentCapabilities, AgentMetadata
from operator_use.acp.server import ACPServer, AgentRunnerFn
from operator_use.bus.views import (
    IncomingMessage,
    OutgoingMessage,
    StreamPhase,
    TextPart,
    text_from_parts,
)
from operator_use.gateway.channels.base import BaseChannel

logger = logging.getLogger(__name__)


class ACPChannel(BaseChannel):
    """ACP server as a gateway channel.

    External ACP clients send runs → converted to IncomingMessages on the bus.
    Each run is routed to the correct agent via account_id on the IncomingMessage.
    Agent responses (OutgoingMessages) → collected and surfaced back to the ACP client
    via the run output / SSE stream.

    Pass `agents` (dict[str, Agent]) to expose all agents via GET /agents and enable
    per-agent routing.
    """

    def __init__(self, config: ACPServerConfig, bus=None, agents: dict | None = None) -> None:
        super().__init__(config, bus)
        # Pending response queues: chat_id (== run_id) -> asyncio.Queue[str | None]
        self._response_queues: dict[str, asyncio.Queue] = {}

        runners, metadata = self._build_runners_and_metadata(config, agents)
        self._server = ACPServer(config=config, runners=runners, metadata=metadata)

    # ------------------------------------------------------------------
    # Builder helpers
    # ------------------------------------------------------------------

    def _build_runners_and_metadata(
        self,
        config: ACPServerConfig,
        agents: dict | None,
    ) -> tuple[dict[str, AgentRunnerFn], dict[str, AgentMetadata]]:
        """Build per-agent runners and metadata from the agents registry."""
        agents = agents or {}
        runners: dict[str, AgentRunnerFn] = {aid: self._make_runner(aid) for aid in agents}
        metadata: dict[str, AgentMetadata] = {
            aid: AgentMetadata(
                id=aid,
                name=aid,
                description=getattr(agent, "description", "") or "",
                capabilities=AgentCapabilities(streaming=True, async_mode=True, session=True),
            )
            for aid, agent in agents.items()
        }
        return runners, metadata

    def _make_runner(self, agent_id: str) -> AgentRunnerFn:
        """Return a runner coroutine-generator bound to a specific agent."""

        async def runner(input_text: str, session_id: str | None):
            import uuid

            run_id = session_id or str(uuid.uuid4())
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            self._response_queues[run_id] = queue

            incoming = IncomingMessage(
                channel=self.name,
                chat_id=run_id,
                # account_id carries the target agent — the Orchestrator router
                # matches this against defn.id to pick the right agent.
                account_id=agent_id,
                parts=[TextPart(content=input_text)],
                user_id=run_id,
                metadata={"acp_session_id": session_id, "run_id": run_id},
            )
            await self.receive(incoming)

            try:
                while True:
                    chunk = await asyncio.wait_for(queue.get(), timeout=120.0)
                    if chunk is None:
                        break
                    yield chunk
            except asyncio.TimeoutError:
                logger.warning(
                    f"ACP run {run_id} (agent={agent_id}) timed out waiting for response"
                )
            finally:
                self._response_queues.pop(run_id, None)

        return runner

    @property
    def name(self) -> str:
        return "acp"

    # ------------------------------------------------------------------
    # BaseChannel lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("ACP channel disabled, skipping")
            return
        self.running = True
        await self._listen()

    async def stop(self) -> None:
        self.running = False
        await self._server.stop()
        # Drain all pending response queues
        for q in self._response_queues.values():
            await q.put(None)
        self._response_queues.clear()

    async def _listen(self) -> None:
        """Start the ACP HTTP server (non-blocking — runs in background)."""
        await self._server.start()

    # ------------------------------------------------------------------
    # Outgoing: agent → ACP client
    # ------------------------------------------------------------------

    async def send(self, message: OutgoingMessage) -> int | None:
        """Receive agent output and forward to the waiting ACP run queue."""
        run_id = message.chat_id
        queue = self._response_queues.get(run_id)
        if not queue:
            logger.debug(f"ACP channel: no queue for run {run_id}, dropping message")
            return None

        phase = message.stream_phase

        if phase in (StreamPhase.CHUNK, StreamPhase.END, None):
            text = text_from_parts(message.parts or [])
            if text:
                await queue.put(text)

        if phase in (StreamPhase.END, StreamPhase.DONE) or phase is None:
            # Signal end of response
            await queue.put(None)

        return None
