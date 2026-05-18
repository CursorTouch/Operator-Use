from __future__ import annotations

import asyncio
import logging
from typing import Callable

from program.gateway.types import BaseChannel, GatewayEvent
from program.hooks.types import (
    AgentEndEvent, AgentErrorEvent, AgentStartEvent,
    MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent,
)
from program.message.types import Role
from program.runtime.service import Runtime

logger = logging.getLogger(__name__)


class Gateway:
    """
    Routes user input from registered channels through the Runtime and
    streams agent events back to the originating channel.

    Only one invocation runs at a time — concurrent send() calls queue behind
    an asyncio.Lock. The lock is acquired per-send so that each channel owns
    the event stream until the agent settles.

    Usage:
        gateway = Gateway(runtime)
        gateway.register(channel)
        await gateway.send(channel.channel_id, "hello")
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._lock = asyncio.Lock()
        self._channels: dict[str, BaseChannel] = {}
        self._active_channel: BaseChannel | None = None
        # tool_call_id → tool_name, tracked across start/end events
        self._pending_tools: dict[str, str] = {}

    # ── Channel management ────────────────────────────────────────────────────

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.channel_id] = channel

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)

    # ── Core dispatch ─────────────────────────────────────────────────────────

    async def send(self, channel_id: str, text: str) -> None:
        """
        Submit user input from the given channel and stream events back.
        Blocks until the agent finishes processing this turn.
        """
        channel = self._channels.get(channel_id)
        if channel is None:
            raise KeyError(f"Unknown channel: {channel_id!r}")

        async with self._lock:
            agent = self._runtime.current_session
            if agent is None:
                await channel.on_event(GatewayEvent(type='error', data={'message': 'No active agent session.'}))
                return

            self._active_channel = channel
            self._pending_tools = {}
            unsub = agent.hooks.subscribe(self._handle_event)
            try:
                await self._runtime.user_input(text)
                await channel.on_event(GatewayEvent(type='done', data={}))
            except Exception as exc:
                logger.exception("Gateway error during send from channel %r", channel_id)
                await channel.on_event(GatewayEvent(type='error', data={'message': str(exc)}))
            finally:
                unsub()
                self._active_channel = None
                self._pending_tools = {}

    # ── Event routing ─────────────────────────────────────────────────────────

    async def _handle_event(self, event) -> None:
        ch = self._active_channel
        if ch is None:
            return

        match event:
            case AgentStartEvent():
                await ch.on_event(GatewayEvent(type='stream_start', data={}))

            case MessageUpdateEvent(message=msg) if msg.role == Role.ASSISTANT:
                for content in msg.contents:
                    text = getattr(content, 'content', '')
                    kind = getattr(content, 'type', '')
                    if text and kind in ('text', 'thinking'):
                        await ch.on_event(GatewayEvent(
                            type='chunk',
                            data={'text': text, 'kind': kind},
                        ))

            case MessageEndEvent(message=msg) if msg.role == Role.ASSISTANT:
                await ch.on_event(GatewayEvent(type='stream_end', data={}))

            case ToolExecutionStartEvent(tool_call=tc):
                self._pending_tools[tc.id] = tc.name
                await ch.on_event(GatewayEvent(
                    type='tool_start',
                    data={'name': tc.name, 'args': tc.args},
                ))

            case ToolExecutionEndEvent(tool_result=res):
                name = self._pending_tools.pop(res.id, '')
                result = str(res.content)
                await ch.on_event(GatewayEvent(
                    type='tool_end',
                    data={'name': name, 'result': result, 'is_error': res.is_error},
                ))

            case AgentErrorEvent(error=err):
                await ch.on_event(GatewayEvent(type='error', data={'message': err}))
