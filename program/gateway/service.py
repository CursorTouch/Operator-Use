from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from program.gateway.types import BaseChannel, GatewayEvent
from program.hooks.service import Hooks
from program.hooks.types import (
    AgentEndEvent, AgentErrorEvent, AgentStartEvent,
    MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent,
    ChannelConnectEvent, ChannelConnectResult,
    ChannelDisconnectEvent,
    MessageReceiveEvent, MessageReceiveResult,
    MessageSendEvent,
    GatewayErrorEvent,
)
from program.message.types import Role

if TYPE_CHECKING:
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
        self._pending_tools: dict[str, str] = {}
        self.hooks = Hooks()

    # ── Channel management ────────────────────────────────────────────────────

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.channel_id] = channel
        asyncio.get_event_loop().create_task(
            self.hooks.emit(ChannelConnectEvent(channel_id=channel.channel_id))
        )

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)
        asyncio.get_event_loop().create_task(
            self.hooks.emit(ChannelDisconnectEvent(channel_id=channel_id))
        )

    @property
    def active_channel_id(self) -> str | None:
        return self._active_channel.channel_id if self._active_channel else None

    # ── Core dispatch ─────────────────────────────────────────────────────────

    async def inject(self, channel_id: str, message: str) -> None:
        """
        Run an out-of-band agent turn and route the output to a specific channel.

        Used for subagent result delivery: acquires the lock, sets the active channel,
        subscribes hooks, then calls agent.invoke() so the LLM response is streamed
        back to the correct channel just like a normal turn.
        """
        from program.agent.types import PromptOptions

        channel = self._channels.get(channel_id)
        if channel is None:
            logger.warning("Gateway.inject: unknown channel %r — dropping message", channel_id)
            return

        agent = self._runtime.current_session
        if agent is None:
            logger.warning("Gateway.inject: no active agent session — dropping message")
            return

        async with self._lock:
            self._active_channel = channel
            self._pending_tools = {}
            unsub = agent.hooks.subscribe(self._handle_event)
            try:
                await agent.invoke(message, PromptOptions(source='subagent'))
                await channel.on_event(GatewayEvent(type='done', data={}))
            except Exception as exc:
                logger.exception("Gateway.inject: agent.invoke failed for channel %r", channel_id)
                await channel.on_event(GatewayEvent(type='error', data={'message': str(exc)}))
            finally:
                unsub()
                self._active_channel = None
                self._pending_tools = {}

    async def send(self, channel_id: str, text: str) -> None:
        """
        Submit user input from the given channel and stream events back.
        Blocks until the agent finishes processing this turn.
        """
        channel = self._channels.get(channel_id)
        if channel is None:
            raise KeyError(f"Unknown channel: {channel_id!r}")

        # ── message:receive hook — allow reject or transform before agent sees it ──
        results = await self.hooks.emit(MessageReceiveEvent(channel_id=channel_id, text=text))
        for r in results:
            if not isinstance(r, MessageReceiveResult):
                continue
            if r.action == 'reject':
                if r.reason:
                    await channel.send(r.reason)
                logger.info("Gateway: message from %r rejected by hook", channel_id)
                return
            if r.action == 'transform' and r.text is not None:
                text = r.text

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
                err_msg = str(exc)
                await channel.on_event(GatewayEvent(type='error', data={'message': err_msg}))
                await self.hooks.emit(GatewayErrorEvent(channel_id=channel_id, error=err_msg))
            finally:
                unsub()
                self._active_channel = None
                self._pending_tools = {}

        # ── message:send hook — fired after the full response is delivered ────────
        await self.hooks.emit(MessageSendEvent(channel_id=channel_id, text=text))

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
