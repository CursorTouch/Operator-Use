from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from program.bus.service import Bus
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart
from program.commands.types import parse_command
from program.gateway.types import BaseChannel
from program.hooks.service import Hooks
from program.subagent.manager import _session_channel, _session_chat_id, _session_message_id
from program.hooks.types import (
    AgentErrorEvent, MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent,
    ChannelConnectEvent, ChannelDisconnectEvent,
    MessageReceiveEvent, MessageReceiveResult,
    MessageSendEvent, MessageSendResult, MessageCancelEvent,
    GatewayErrorEvent,
)
from program.message.types import UserMessage, Role
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
        self._direct_handlers: dict[str, Callable[[IncomingMessage], Awaitable[None]]] = {}
        self._incoming_loop_task: asyncio.Task | None = None
        self._outgoing_loop_task: asyncio.Task | None = None
        self.hooks = Hooks()

    # ── Direct handler registration ───────────────────────────────────────────

    def register_direct_handler(
        self,
        channel_id: str,
        handler: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        """Register a handler that receives all incoming messages for channel_id
        directly, bypassing gateway session management entirely."""
        self._direct_handlers[channel_id] = handler

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
        # Wait for both loops; re-raise any exceptions
        await asyncio.gather(self._incoming_loop_task, self._outgoing_loop_task)

    async def stop(self) -> None:
        """Cancel all processing loop tasks."""
        for attr in ('_incoming_loop_task', '_outgoing_loop_task'):
            task = getattr(self, attr, None)
            if task is not None:
                task.cancel()
                setattr(self, attr, None)

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
        """Handle one incoming message: hook → get/create session → run.

        Channels registered via register_direct_handler() bypass gateway session
        management entirely and are delivered straight to their handler.
        """
        handler = self._direct_handlers.get(msg.channel)
        if handler is not None:
            await handler(msg)
            return

        from program.bus.types import AudioPart
        parts = list(msg.parts)

        # ── message:receive hook — STT hooks detect AudioPart here and return
        # transformed parts (AudioPart → TextPart). Reject/transform text also handled.
        text = "\n".join(p.content for p in parts if isinstance(p, TextPart))
        results = await self.hooks.emit(
            MessageReceiveEvent(
                channel_id=msg.channel,
                chat_id=msg.chat_id,
                user_id=msg.user_id,
                text=text,
                parts=parts,
            )
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
            if r.action == 'transform':
                if r.parts is not None:
                    parts = r.parts
                if r.text is not None:
                    text = r.text

        # Re-extract text from parts in case STT hook replaced AudioPart with TextPart
        if any(r for r in results if isinstance(r, MessageReceiveResult) and r.action == 'transform' and r.parts is not None):
            text = "\n".join(p.content for p in parts if isinstance(p, TextPart))

        is_voice = any(isinstance(p, AudioPart) for p in msg.parts)

        parsed = parse_command(text)
        if parsed is not None:
            await self._run_command(msg.channel, msg.chat_id, parsed)
            return

        session_key = f"{msg.channel}:{msg.chat_id}"
        entry = self._get_or_create_session(session_key, channel_id=msg.channel, chat_id=msg.chat_id)

        if entry.task is not None and not entry.task.done():
            await entry.agent._engine.steer(UserMessage.text(text))
            return

        entry.task = asyncio.create_task(
            self._run_session(
                session_key, msg.channel, msg.chat_id, entry.agent, text,
                user_id=msg.user_id,
                parts=list(msg.parts),
                msg_metadata=msg.metadata,
                is_voice=is_voice,
                message_id=msg.message_id or None,
            ),
            name=f'gateway:session:{session_key}',
        )

    # ── File sending ─────────────────────────────────────────────────────────

    async def send_file(
        self,
        channel_id: str,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> None:
        """Send a local file to a channel/chat as an out-of-band message."""
        from program.bus.types import FilePart
        parts: list = [FilePart(path=file_path, mime_type=mime_type)]
        if caption:
            parts.append(TextPart(caption))
        msg = OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            parts=parts,
        )
        channel = self._channels.get(channel_id)
        if channel is not None:
            await channel.send(msg)
        else:
            logger.warning("Gateway.send_file: unknown channel %r — dropping", channel_id)

    # ── Session management ────────────────────────────────────────────────────

    def _get_or_create_session(self, session_key: str, channel_id: str | None = None, chat_id: str | None = None) -> _SessionEntry:
        if session_key not in self._sessions:
            # `unified_session` (default True) shares the REPL agent across all channels.
            # When False, each channel:chat_id pair gets its own isolated agent.
            settings = self._runtime._context.settings_manager
            unified = True
            if settings is not None and settings.settings.unified_session is not None:
                unified = settings.settings.unified_session
            if unified:
                agent = self._runtime.current_session
                if agent is None:
                    raise RuntimeError("No active session available.")
            else:
                agent = self._runtime.create_session_agent()
            self._sessions[session_key] = _SessionEntry(agent=agent)
        return self._sessions[session_key]

    async def cancel_session(self, channel_id: str, chat_id: str) -> None:
        """Hard-cancel an in-progress session and fire MessageCancelEvent."""
        session_key = f"{channel_id}:{chat_id}"
        entry = self._sessions.get(session_key)
        if entry is None or entry.task is None or entry.task.done():
            return
        entry.task.cancel()
        try:
            await entry.task
        except (asyncio.CancelledError, Exception):
            pass
        # Flush the channel buffer so the partial stream is cleared.
        await self._bus.publish_outgoing(OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            stream_phase=StreamPhase.END,
        ))
        await self.hooks.emit(MessageCancelEvent(channel_id=channel_id, chat_id=chat_id))

    # ── Command runner ────────────────────────────────────────────────────────

    async def _run_command(self, channel_id: str, chat_id: str, parsed) -> None:
        """Dispatch a slash command and send its printed output back to the channel."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            await self._runtime.commands.dispatch(parsed)
        output = buf.getvalue().strip()
        if output:
            await self._bus.publish_outgoing(OutgoingMessage(
                channel=channel_id,
                chat_id=chat_id,
                parts=[TextPart(output)],
            ))

    # ── Session runner ────────────────────────────────────────────────────────

    async def _run_session(
        self,
        session_key: str,
        channel_id: str,
        chat_id: str,
        agent: Agent,
        text: str,
        user_id: str = '',
        parts: list | None = None,
        msg_metadata: dict | None = None,
        is_voice: bool = False,
        message_id: str | None = None,
    ) -> None:
        """Invoke the agent and publish OutgoingMessage events to the bus."""
        from program.agent.types import PromptOptions

        response_parts: list[str] = []

        async def _on_event(event) -> None:
            match event:
                case MessageUpdateEvent(message=m) if m.role == Role.ASSISTANT:
                    for content in m.contents:
                        chunk_text = getattr(content, 'content', '')
                        kind = getattr(content, 'type', '')
                        if chunk_text and kind in ('text', 'thinking'):
                            if kind == 'text':
                                response_parts.append(chunk_text)
                            out = OutgoingMessage(
                                channel=channel_id,
                                chat_id=chat_id,
                                parts=[TextPart(chunk_text)],
                                stream_phase=StreamPhase.CHUNK,
                                metadata={'kind': kind},
                            )
                            await self._bus.publish_outgoing(out)

                case MessageEndEvent(message=m) if m is not None and m.role == Role.ASSISTANT:
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.END,
                        metadata={'origin_message_id': message_id} if message_id else {},
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

        # Write a ChannelEntry when the active channel changes.
        if agent._session_manager.get_current_channel() != channel_id:
            agent._session_manager.append_channel_entry(channel_id, chat_id, user_id or None)

        # Build per-message meta: attachments from non-text parts, reply_to if present.
        from program.session.types import MessageMeta, MessageAttachment
        from program.bus.types import AudioPart, FilePart
        attachments = [
            MessageAttachment(path=getattr(p, 'audio', None) or getattr(p, 'path', ''), mime_type=getattr(p, 'mime_type', None))
            for p in (parts or [])
            if isinstance(p, (AudioPart, FilePart))
        ]
        reply_to = (msg_metadata or {}).get('reply_to')
        msg_meta = MessageMeta(
            reply_to=str(reply_to) if reply_to else None,
            attachments=attachments or None,
        )

        # Expose channel + chat_id + message_id via contextvars so tools (send,
        # subagent) know where to deliver results and which message to react to.
        _session_channel.set(channel_id)
        _session_chat_id.set(chat_id)
        _session_message_id.set(message_id)

        unsub = agent.hooks.subscribe(_on_event)
        try:
            await agent.invoke(text, PromptOptions(source='interactive', meta=msg_meta))
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

        # ── message:send hook — fired before DONE so TTS hooks can inject audio.
        # is_voice=True when the original message had an AudioPart, letting TTS hooks
        # gate synthesis on voice-originated messages only.
        response_text = "".join(response_parts)
        send_results = await self.hooks.emit(MessageSendEvent(
            channel_id=channel_id,
            chat_id=chat_id,
            input_text=text,
            response_text=response_text,
            is_voice=is_voice,
        ))
        for r in send_results:
            if isinstance(r, MessageSendResult) and r.parts:
                channel = self._channels.get(channel_id)
                if channel is not None:
                    audio_out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=r.parts,
                    )
                    await channel.send(audio_out)

        # Publish DONE
        await self._bus.publish_outgoing(OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            stream_phase=StreamPhase.DONE,
        ))

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
