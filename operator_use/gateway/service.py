from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from operator_use.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart
from operator_use.commands.types import parse_command
from operator_use.gateway.types import BaseChannel
from operator_use.hooks.service import Hooks
from operator_use.subagent.manager import _session_channel, _session_chat_id, _session_message_id
from operator_use.agent.types import RetryStartEvent, RetryEndEvent
from operator_use.hooks.types import (
    AgentErrorEvent, MessageEndEvent, MessageUpdateEvent,
    ToolExecutionEndEvent, ToolExecutionStartEvent, ToolExecutionUpdateEvent,
    ChannelConnectEvent, ChannelDisconnectEvent,
    MessageReceiveEvent, MessageReceiveResult,
    MessageSendEvent, MessageSendResult, MessageCancelEvent,
    GatewayErrorEvent,
)
from operator_use.message.types import UserMessage, Role
from operator_use.inference.types import StopReason
if TYPE_CHECKING:
    from operator_use.runtime.service import Runtime
    from operator_use.agent.service import Agent

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
        gateway = Gateway(runtime)
        gateway.register(channel)
        await gateway.start()
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._bus = runtime.bus
        self._channels: dict[str, BaseChannel] = {}
        self._sessions: dict[str, _SessionEntry] = {}
        self._profile_agents: dict[str, Agent] = {}  # profile_name → dedicated Agent
        self._direct_handlers: dict[str, Callable[[IncomingMessage], Awaitable[None]]] = {}
        self._handler_tasks: set[asyncio.Task] = set()
        self._incoming_loop_task: asyncio.Task | None = None
        self._outgoing_loop_task: asyncio.Task | None = None
        self.hooks = Hooks()

    # ── Profile agent registration ────────────────────────────────────────────

    def register_profile_agent(self, profile_name: str, agent: Agent) -> None:
        """Register a dedicated agent for a named profile.

        All channels whose channel_id starts with '{profile_name}:' will share
        this single agent (unified session per profile).
        """
        self._profile_agents[profile_name] = agent

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

    def _spawn(self, coro) -> None:
        # Retain a strong ref: the loop only holds a weak ref, so a discarded
        # fire-and-forget task can be GC'd before it runs.
        task = asyncio.get_event_loop().create_task(coro)
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    def register(self, channel: BaseChannel) -> None:
        channel.bus = self._bus
        self._channels[channel.channel_id] = channel
        self._spawn(self.hooks.emit(ChannelConnectEvent(channel_id=channel.channel_id)))

    def unregister(self, channel_id: str) -> None:
        self._channels.pop(channel_id, None)
        self._spawn(self.hooks.emit(ChannelDisconnectEvent(channel_id=channel_id)))

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
        """Cancel processing loops and any in-flight agent session tasks."""
        tasks: list[asyncio.Task] = []
        current = asyncio.current_task()

        for attr in ('_incoming_loop_task', '_outgoing_loop_task'):
            task = getattr(self, attr, None)
            if task is not None:
                if task is not current and not task.done():
                    task.cancel()
                    tasks.append(task)
                setattr(self, attr, None)

        for task in list(self._handler_tasks):
            if task is current or task.done():
                continue
            task.cancel()
            tasks.append(task)

        for entry in self._sessions.values():
            if entry.task is None or entry.task is current or entry.task.done():
                continue
            entry.agent.shutdown()
            entry.task.cancel()
            tasks.append(entry.task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── Incoming message loop ─────────────────────────────────────────────────

    async def _incoming_loop(self) -> None:
        """Consume incoming messages from the bus and spawn per-message tasks."""
        while True:
            try:
                msg = await self._bus.consume_incoming()
                task = asyncio.create_task(self._handle_incoming(msg))
                self._handler_tasks.add(task)
                task.add_done_callback(self._handler_tasks.discard)
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

        from operator_use.bus.types import AudioPart
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
            match r.action:
                case 'reject':
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
                case 'transform':
                    if r.parts is not None:
                        parts = r.parts
                    if r.text is not None:
                        text = r.text
                case 'continue':
                    pass

        # Re-extract text from parts in case STT hook replaced AudioPart with TextPart
        if any(r for r in results if isinstance(r, MessageReceiveResult) and r.action == 'transform' and r.parts is not None):
            text = "\n".join(p.content for p in parts if isinstance(p, TextPart))

        is_voice = any(isinstance(p, AudioPart) for p in msg.parts)

        parsed = parse_command(text)
        if parsed is not None:
            await self._run_command(msg.channel, msg.chat_id, parsed)
            return

        # Profile channels (format: '{profile_name}:{channel_type}') share one
        # session across all their channels and chat_ids.
        session_key = self._session_key(msg.channel, msg.chat_id)
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
        from operator_use.bus.types import FilePart
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

    def _session_key(self, channel_id: str, chat_id: str) -> str:
        """Map a (channel, chat) to its session key. Profile channels
        ('{profile}:{type}') share one session keyed by profile name; everything
        else is keyed per channel+chat. Must match across handle/cancel paths."""
        colon = channel_id.find(':')
        if colon > 0:
            profile_name = channel_id[:colon]
            if profile_name in self._profile_agents:
                return profile_name
        return f"{channel_id}:{chat_id}"

    def _get_or_create_session(self, session_key: str, channel_id: str | None = None, chat_id: str | None = None) -> _SessionEntry:
        if session_key not in self._sessions:
            agent = self._resolve_agent_for_channel(channel_id)
            self._sessions[session_key] = _SessionEntry(agent=agent)
        return self._sessions[session_key]

    def _resolve_agent_for_channel(self, channel_id: str | None) -> Agent:
        """Resolve which agent handles a channel.

        Profile channels use the format '{profile_name}:{channel_type}'.
        Non-profile channels fall back to the runtime's unified/per-session logic.
        """
        if channel_id:
            colon = channel_id.find(':')
            if colon > 0:
                profile_name = channel_id[:colon]
                profile_agent = self._profile_agents.get(profile_name)
                if profile_agent is not None:
                    return profile_agent

        # Fall back to original logic for non-profile channels
        if self._runtime.unified_session_enabled:
            agent = self._runtime.current_session
            if agent is None:
                raise RuntimeError("No active session available.")
            return agent
        return self._runtime.create_session_agent()

    def new_profile_session(self, profile_name: str) -> None:
        """Start a new session for a profile without deleting the existing one.

        The previous session file stays on disk under profile.sessions_dir and
        remains accessible. A fresh JSONL file will be created there on the
        first assistant message of the new session.

        The profile Agent is kept alive; only the in-memory session state and
        the gateway session-cache entry are replaced so the next incoming
        message starts a new conversation.
        """
        # Drop the gateway session-cache entry so the next message gets a
        # fresh _SessionEntry pointing at the same agent with its new session.
        keys_to_remove = [k for k in self._sessions if k == profile_name or k.startswith(f'{profile_name}:')]
        for k in keys_to_remove:
            self._sessions.pop(k, None)
        agent = self._profile_agents.get(profile_name)
        if agent is not None:
            # new_session() allocates a new file path in sessions_dir and clears
            # in-memory entries; it does NOT delete or overwrite any existing file.
            agent._session_manager.new_session()

    async def cancel_session(self, channel_id: str, chat_id: str) -> None:
        """Hard-cancel an in-progress session and fire MessageCancelEvent."""
        session_key = self._session_key(channel_id, chat_id)
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
        """Dispatch a slash command and send its printed output back to the channel.

        /new from a profile channel resets that profile's session instead of
        the REPL session, since profile agents are independent of the runtime.
        """
        # Intercept /new (and its alias /clear) for profile channels so it
        # resets the profile's own session, not the main REPL session.
        if parsed.name in ('new', 'clear'):
            colon = channel_id.find(':')
            if colon > 0:
                profile_name = channel_id[:colon]
                if profile_name in self._profile_agents:
                    self.new_profile_session(profile_name)
                    await self._bus.publish_outgoing(OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=[TextPart('Session cleared.')],
                    ))
                    return

        output = await self.dispatch_command(parsed)
        if output:
            await self._bus.publish_outgoing(OutgoingMessage(
                channel=channel_id,
                chat_id=chat_id,
                parts=[TextPart(output)],
            ))

    async def dispatch_command(self, parsed) -> str:
        """Dispatch a slash command and return the text it printed."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            await self._runtime.commands.dispatch(parsed)
        return buf.getvalue().strip()

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
        from operator_use.agent.types import PromptOptions

        response_parts: list[str] = []
        # Tracks the last error text seen from the engine and retry metadata.
        # Wrapped in lists/dicts for mutability inside the closure.
        _last_error: list[str] = ['']
        _retry_max: list[int] = [0]  # total attempts (max_retries + 1)
        _tool_names: dict[str, str] = {}         # id → name
        _tool_display_names: dict[str, str] = {}  # id → display_name from tool_start

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
                    # Flush the buffered text on every turn boundary, but only ask the
                    # channel to stop its typing indicator when the model produced a
                    # final answer (stop_reason == Stop). For tool_calls the typing
                    # indicator must keep running through tool execution. For Error/Abort
                    # the engine emits message=None (filtered by the guard above), and
                    # the retry/final-error flow handles the indicator via ERROR.
                    meta: dict = {'origin_message_id': message_id} if message_id else {}
                    if m.stop_reason != StopReason.Stop:
                        meta['keep_typing'] = True
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.END,
                        metadata=meta,
                    )
                    await self._bus.publish_outgoing(out)

                case ToolExecutionStartEvent(tool_call=tc):
                    _tool_names[tc.id] = tc.name
                    _tool_display_names[tc.id] = tc.metadata.get('display_name', '')
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.CHUNK,
                        metadata={'kind': 'tool_start', 'name': tc.name, 'display_name': tc.metadata.get('display_name', ''), 'args': tc.args, 'id': tc.id, 'tool_kind': tc.kind.value if tc.kind else None},
                    )
                    await self._bus.publish_outgoing(out)

                case ToolExecutionUpdateEvent(partial_tool_result=partial) if partial is not None:
                    name = _tool_names.get(partial.id, '')
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.CHUNK,
                        metadata={'kind': 'tool_update', 'name': name, 'text': str(partial.content), 'id': partial.id},
                    )
                    await self._bus.publish_outgoing(out)

                case ToolExecutionEndEvent(tool_result=res):
                    # Emit for both success and error so channels can flip the rolling
                    # tool-status message to ✅ / ❌. ToolResultContent doesn't carry the
                    # tool name, so look it up from the id→name map populated on start.
                    name = _tool_names.pop(res.id, '')
                    # Tools can set display_name in their result metadata to override the
                    # end label (e.g. "Changed setting: memory" vs "control_center").
                    display_name = res.metadata.get('display_name', '') or _tool_display_names.pop(res.id, '')
                    out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.CHUNK,
                        metadata={
                            'kind': 'tool_end',
                            'name': name,
                            'display_name': display_name,
                            'is_error': res.is_error,
                            'result': str(res.content)[:300] if res.is_error else '',
                            'id': res.id,
                        },
                    )
                    await self._bus.publish_outgoing(out)

                case AgentErrorEvent(error=err):
                    # Store the error; it will be surfaced via RetryEndEvent (mid-retry)
                    # or the final except block (exhausted/permanent). Posting here would
                    # show a duplicate for every retry attempt.
                    _last_error[0] = err

                case RetryStartEvent(max_retries=mx):
                    _retry_max[0] = mx + 1
                    # Restart the typing indicator so it stays on during the retry delay
                    # and the next attempt.
                    await self._bus.publish_outgoing(OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.START,
                    ))

                case RetryEndEvent(success=True):
                    # A retry succeeded — ask the channel to silently remove its rolling
                    # retry-status message so the success response stands on its own.
                    await self._bus.publish_outgoing(OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        stream_phase=StreamPhase.ERROR,
                        metadata={'retry': True, 'retry_success': True},
                    ))

                case RetryEndEvent(attempt=att, success=False, error=err):
                    # Mid-retry: replace (edit) the previous retry-status message rather
                    # than stacking a new one for every attempt.
                    await self._bus.publish_outgoing(OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=[TextPart(err or _last_error[0])],
                        stream_phase=StreamPhase.ERROR,
                        metadata={
                            'retry': True,
                            'retry_attempt': att + 1,
                            'retry_max': _retry_max[0],
                        },
                    ))

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
        from operator_use.session.types import MessageMeta, MessageAttachment
        from operator_use.bus.types import AudioPart, FilePart
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
            await agent.invoke(text, PromptOptions(source='interactive', meta=msg_meta, channel=channel_id))
        except Exception as exc:
            logger.exception("Gateway: agent.invoke failed for session %r", session_key)
            err_msg = _last_error[0] or str(exc)
            await self._bus.publish_outgoing(OutgoingMessage(
                channel=channel_id,
                chat_id=chat_id,
                parts=[TextPart(err_msg)],
                stream_phase=StreamPhase.ERROR,
                metadata={'retry': True, 'retry_final': True},
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
