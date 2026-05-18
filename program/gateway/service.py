from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from program.bus.service import Bus
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart
from program.gateway.types import BaseChannel
from program.hooks.service import Hooks
from program.hooks.types import (
    AgentErrorEvent, MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent,
    ChannelConnectEvent, ChannelDisconnectEvent,
    MessageReceiveEvent, MessageReceiveResult,
    MessageSendEvent,
    GatewayErrorEvent,
)
from program.message.types import Role

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.agent.service import Agent

logger = logging.getLogger(__name__)


@dataclass
class _SessionEntry:
    agent: Agent
    task: asyncio.Task | None = None


class Gateway:
    """
    Routes user input from registered channels through the Runtime and
    streams agent events back to the originating channel via the Bus.

    Usage:
        bus = Bus()
        gateway = Gateway(bus, runtime)
        gateway.register(channel)
        await gateway.start()
    """

    def __init__(self, bus: Bus, runtime: Runtime) -> None:
        self._bus = bus
        self._runtime = runtime
        self._channels: dict[str, BaseChannel] = {}
        self._sessions: dict[str, _SessionEntry] = {}
        self._incoming_loop_task: asyncio.Task | None = None
        self._outgoing_loop_task: asyncio.Task | None = None
        self.hooks = Hooks()

    # ── Channel management ────────────────────────────────────────────────────

    def register(self, channel: BaseChannel) -> None:
        channel.bus = self._bus
        self._channels[channel.channel_id] = channel
        asyncio.get_event_loop().create_task(
            self.hooks.emit(ChannelConnectEvent(channel_id=channel.channel_id))
        )

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)
        asyncio.get_event_loop().create_task(
            self.hooks.emit(ChannelDisconnectEvent(channel_id=channel_id))
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the incoming and outgoing processing loops."""
        self._incoming_loop_task = asyncio.create_task(
            self._incoming_loop(), name='gateway:incoming_loop'
        )
        self._outgoing_loop_task = asyncio.create_task(
            self._outgoing_loop(), name='gateway:outgoing_loop'
        )
        # Wait for both tasks; re-raise any exceptions
        await asyncio.gather(self._incoming_loop_task, self._outgoing_loop_task)

    async def stop(self) -> None:
        """Cancel both processing loop tasks."""
        if self._incoming_loop_task is not None:
            self._incoming_loop_task.cancel()
            self._incoming_loop_task = None
        if self._outgoing_loop_task is not None:
            self._outgoing_loop_task.cancel()
            self._outgoing_loop_task = None

    # ── Incoming message loop ─────────────────────────────────────────────────

    async def _incoming_loop(self) -> None:
        """Consume incoming messages from the bus and spawn per-message tasks."""
        while True:
            try:
                msg = await self._bus.consume_incoming()
                asyncio.create_task(self._handle_incoming(msg))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Gateway: error in incoming loop")

    async def _handle_incoming(self, msg: IncomingMessage) -> None:
        """Handle one incoming message: hook → get/create session → run."""
        from program.message.types import Role

        # ── message:receive hook — allow reject or transform before agent sees it
        text = "\n".join(p.content for p in msg.parts if isinstance(p, TextPart))
        results = await self.hooks.emit(
            MessageReceiveEvent(channel_id=msg.channel, text=text)
        )
        for r in results:
            if not isinstance(r, MessageReceiveResult):
                continue
            if r.action == 'reject':
                if r.reason:
                    ch = self._channels.get(msg.channel)
                    if ch is not None:
                        reject_msg = OutgoingMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            parts=[TextPart(r.reason)],
                        )
                        await ch.send(reject_msg)
                logger.info("Gateway: message from %r rejected by hook", msg.channel)
                return
            if r.action == 'transform' and r.text is not None:
                text = r.text

        session_key = f"{msg.channel}:{msg.chat_id}"
        entry = self._get_or_create_session(session_key)

        # Cancel any currently running task for this session
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()

        entry.task = asyncio.create_task(
            self._run_session(session_key, msg.channel, msg.chat_id, entry.agent, text),
            name=f'gateway:session:{session_key}',
        )

    # ── Session management ────────────────────────────────────────────────────

    def _get_or_create_session(self, session_key: str) -> _SessionEntry:
        if session_key not in self._sessions:
            agent = self._runtime.create_session_agent()
            self._sessions[session_key] = _SessionEntry(agent=agent)
        return self._sessions[session_key]

    # ── Session runner ────────────────────────────────────────────────────────

    async def _run_session(
        self,
        session_key: str,
        channel_id: str,
        chat_id: str,
        agent: Agent,
        text: str,
    ) -> None:
        """Invoke the agent and publish OutgoingMessage events to the bus."""
        from program.agent.types import PromptOptions

        async def _on_event(event) -> None:
            match event:
                case MessageUpdateEvent(message=m) if m.role == Role.ASSISTANT:
                    for content in m.contents:
                        chunk_text = getattr(content, 'content', '')
                        kind = getattr(content, 'type', '')
                        if chunk_text and kind in ('text', 'thinking'):
                            out = OutgoingMessage(
                                channel=channel_id,
                                chat_id=chat_id,
                                parts=[TextPart(chunk_text)],
                                stream_phase=StreamPhase.CHUNK,
                                metadata={'kind': kind},
                            )
                            await self._bus.publish_outgoing(out)

                case MessageEndEvent(message=m) if m.role == Role.ASSISTANT:
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.END,
                    )
                    await self._bus.publish_outgoing(out)

                case ToolExecutionStartEvent(tool_call=tc):
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.CHUNK,
                        metadata={'kind': 'tool_start', 'name': tc.name, 'args': tc.args},
                    )
                    await self._bus.publish_outgoing(out)

                case ToolExecutionEndEvent(tool_result=res) if res.is_error:
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.CHUNK,
                        metadata={
                            'kind': 'tool_end',
                            'is_error': True,
                            'result': str(res.content)[:300],
                        },
                    )
                    await self._bus.publish_outgoing(out)

                case AgentErrorEvent(error=err):
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=[TextPart(str(err))],
                        stream_phase=StreamPhase.ERROR,
                    )
                    await self._bus.publish_outgoing(out)

        # Publish START
        await self._bus.publish_outgoing(OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            stream_phase=StreamPhase.START,
        ))

        unsub = agent.hooks.subscribe(_on_event)
        try:
            await agent.invoke(text, PromptOptions(source='interactive'))
        except Exception as exc:
            logger.exception("Gateway: agent.invoke failed for session %r", session_key)
            err_msg = str(exc)
            await self._bus.publish_outgoing(OutgoingMessage(
                channel=channel_id,
                chat_id=chat_id,
                parts=[TextPart(err_msg)],
                stream_phase=StreamPhase.ERROR,
            ))
            await self.hooks.emit(GatewayErrorEvent(channel_id=channel_id, error=err_msg))
        finally:
            unsub()

        # Publish DONE
        await self._bus.publish_outgoing(OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            stream_phase=StreamPhase.DONE,
        ))

        # ── message:send hook — fired after full response delivered
        await self.hooks.emit(MessageSendEvent(channel_id=channel_id, text=text))

    # ── Outgoing message loop ─────────────────────────────────────────────────

    async def _outgoing_loop(self) -> None:
        """Consume outgoing messages and route to the appropriate channel."""
        while True:
            try:
                try:
                    msg = await asyncio.wait_for(
                        self._bus.consume_outgoing(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                channel = self._channels.get(msg.channel)
                if channel is None:
                    logger.debug(
                        "Gateway: outgoing message for unknown channel %r — dropping",
                        msg.channel,
                    )
                    continue

                try:
                    await channel.send(msg)
                except Exception:
                    logger.exception(
                        "Gateway: channel.send() failed for channel %r", msg.channel
                    )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Gateway: error in outgoing loop")
