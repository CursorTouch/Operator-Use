from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from operator_use.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart

# Pending permission futures: (channel, chat_id) → Future[IncomingMessage]
# Populated by OperatorACPClient when awaiting a human permission decision.
_PERMISSION_FUTURES: dict[tuple[str, str], asyncio.Future] = {}


def register_permission_future(channel: str, chat_id: str, fut: asyncio.Future) -> None:
    """Register a Future that will be resolved when the user replies to a permission prompt."""
    _PERMISSION_FUTURES[(channel, chat_id)] = fut


def unregister_permission_future(channel: str, chat_id: str) -> None:
    """Remove a previously registered permission Future (e.g. on timeout or cancellation)."""
    _PERMISSION_FUTURES.pop((channel, chat_id), None)


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
    """Pairs a session's Agent with the asyncio Task currently processing a message for it."""

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
        """Attach to the runtime's bus and initialise empty channel/session registries."""
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

    def _profile_agent_for_channel(self, channel_id: str | None) -> Agent | None:
        """Return the profile agent for a profile-namespaced channel id, or None."""
        if not channel_id:
            return None
        colon = channel_id.find(':')
        if colon <= 0:
            return None
        return self._profile_agents.get(channel_id[:colon])

    def _effective_media_enabled(
        self,
        kind: str,
        *,
        channel_id: str | None = None,
        chat_id: str | None = None,
        agent: Agent | None = None,
    ) -> bool | None:
        """Return the enabled flag for a media setting (e.g. 'stt', 'tts') respecting active profile overlays."""
        try:
            from operator_use.settings.manager import SettingsManager
            settings_mgr = SettingsManager.get_instance()
            if settings_mgr is None:
                return None

            resolved_agent = agent
            if resolved_agent is None and channel_id is not None and chat_id is not None:
                entry = self._sessions.get(self._session_key(channel_id, chat_id))
                resolved_agent = entry.agent if entry else None
            if resolved_agent is None:
                resolved_agent = self._profile_agent_for_channel(channel_id)

            profile = (
                resolved_agent.get_active_profile()
                if resolved_agent is not None and hasattr(resolved_agent, 'get_active_profile')
                else None
            )
            if profile is not None:
                settings = settings_mgr.settings_with_profile_overlay(profile.settings_path)
            else:
                settings = settings_mgr.settings

            media_settings = getattr(settings, kind, None)
            return media_settings.enabled if media_settings else None
        except Exception:
            return None

    # ── Channel management ────────────────────────────────────────────────────

    def _spawn(self, coro) -> None:
        """Fire-and-forget a coroutine as a tracked task so it isn't GC'd before completion."""
        # Retain a strong ref: the loop only holds a weak ref, so a discarded
        # fire-and-forget task can be GC'd before it runs.
        task = asyncio.get_event_loop().create_task(coro)
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    def register(self, channel: BaseChannel) -> None:
        """Attach the bus to a channel, add it to the registry, and fire ChannelConnectEvent."""
        channel.bus = self._bus
        self._channels[channel.channel_id] = channel
        self._spawn(self.hooks.emit(ChannelConnectEvent(channel_id=channel.channel_id)))

    def unregister(self, channel_id: str) -> None:
        """Remove a channel from the registry and fire ChannelDisconnectEvent."""
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
            # Signal abort first so the engine can reach a check-point and write
            # the synthetic closing message, then cancel so it doesn't spin forever.
            entry.agent.shutdown()
            entry.task.cancel()
            tasks.append(entry.task)
        # Yield briefly so the engine's abort check-points can fire before the
        # CancelledError is processed in the gather below.
        await asyncio.sleep(0)

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
        # Permission intercept: an OperatorACPClient is awaiting a human decision
        # on this exact channel+chat. Resolve its Future and consume the message.
        perm_fut = _PERMISSION_FUTURES.pop((msg.channel, msg.chat_id), None)
        if perm_fut is not None and not perm_fut.done():
            perm_fut.set_result(msg)
            return

        # Check if this channel has a "direct handler" — a custom function
        # registered to handle messages on its own, without going through
        # the normal agent/session pipeline below.
        # Think of it like a VIP shortcut: some channels (e.g. internal control
        # channels or plugin-owned channels) don't need an AI agent to process
        # their messages — they just want raw delivery to their own logic.
        handler = self._direct_handlers.get(msg.channel)
        if handler is not None:
            # Found one — hand the message directly to it and stop here.
            # Nothing else (session creation, hooks, STT, agent routing) runs.
            await handler(msg)
            return

        # Pull in the AudioPart type so we can check whether this message
        # contains voice/audio content (needed later for STT detection).
        from operator_use.bus.types import AudioPart

        # Snapshot the message parts into a plain list so hooks can safely
        # replace or transform them without mutating the original message.
        parts = list(msg.parts)

        # Extract the plain text from this message (joining multiple text parts
        # with newlines) so hooks can read/modify what the user said.
        text = "\n".join(p.content for p in parts if isinstance(p, TextPart))

        # Check whether speech-to-text (STT) is turned on for this channel+chat.
        # Hooks use this flag to decide if they should transcribe audio parts.
        stt_enabled = self._effective_media_enabled(
            'stt',
            channel_id=msg.channel,
            chat_id=msg.chat_id,
        )

        # Fire the "message received" hook event. Any registered hook gets a chance
        # to inspect the message and either:
        #   • reject it  — block it entirely (e.g. spam filter)
        #   • transform it — swap out parts/text (e.g. STT: audio → text)
        #   • continue  — let it pass through unchanged
        results = await self.hooks.emit(
            MessageReceiveEvent(
                channel_id=msg.channel,
                chat_id=msg.chat_id,
                user_id=msg.user_id,
                text=text,
                parts=parts,
                stt_enabled=stt_enabled,
            )
        )
        # Process each hook's response and act on its decision.
        for r in results:
            if not isinstance(r, MessageReceiveResult):
                continue
            match r.action:
                case 'reject':
                    # A hook said "don't process this message". If it gave a
                    # reason, send that reason back to the user as a reply,
                    # then stop — the agent never sees this message.
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
                    # A hook rewrote the message (e.g. STT converted audio to text).
                    # Update our local parts/text so the rest of the pipeline
                    # works with the transformed version.
                    if r.parts is not None:
                        parts = r.parts
                    if r.text is not None:
                        text = r.text
                case 'continue':
                    # Hook is happy — nothing to change, keep going.
                    pass

        # If a hook swapped the parts (e.g. audio → text), re-build the plain
        # text string from the updated parts so the agent gets the transcript,
        # not the original audio placeholder.
        if any(r for r in results if isinstance(r, MessageReceiveResult) and r.action == 'transform' and r.parts is not None):
            text = "\n".join(p.content for p in parts if isinstance(p, TextPart))

        # Remember whether the *original* message had audio. This lets downstream
        # code (e.g. TTS) know the user spoke rather than typed.
        is_voice = any(isinstance(p, AudioPart) for p in msg.parts)

        # Check if the text is a slash-command (e.g. "/help", "/reset").
        # If so, run the command handler and stop — the agent doesn't see it.
        parsed = parse_command(text)
        if parsed is not None:
            await self._run_command(msg.channel, msg.chat_id, parsed)
            return

        # Build the session key for this conversation. Profile channels
        # (format: 'profile_name:channel_type') intentionally share one session
        # across all their sub-channels — so all of them talk to the same agent.
        session_key = self._session_key(msg.channel, msg.chat_id)

        # Look up the existing session for this conversation, or create a fresh
        # one if this is the first message in this channel+chat.
        entry = self._get_or_create_session(session_key, channel_id=msg.channel, chat_id=msg.chat_id)

        if entry.task is not None and not entry.task.done():
            # The agent is already busy processing a previous message in this
            # session. Instead of queuing a new run, we "steer" the running
            # agent mid-stream — injecting the new user message so it can
            # react to it without starting over.
            await entry.agent._engine.steer(UserMessage.text(text))
            return

        # No active task — kick off a new agent run as a background asyncio task.
        # The task processes the full message, streams replies back to the channel,
        # and marks itself done when finished.
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

        # Wrap the file path in a FilePart so the channel knows it's a file,
        # not plain text. The channel driver handles the actual upload/delivery.
        parts: list = [FilePart(path=file_path, mime_type=mime_type)]

        # Optionally attach a human-readable caption below the file.
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
            # The channel isn't registered — log a warning and drop the message
            # rather than raising, so one bad send doesn't crash anything.
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
        """Return the existing session entry for key, or create and cache a new one."""
        # If we've never seen this session key before, create a brand-new entry.
        # This picks the right agent for the channel (profile agent or default).
        if session_key not in self._sessions:
            agent = self._resolve_agent_for_channel(channel_id)
            self._sessions[session_key] = _SessionEntry(agent=agent)
        # Return the existing (or just-created) session entry.
        return self._sessions[session_key]

    def _resolve_agent_for_channel(self, channel_id: str | None) -> Agent:
        """Resolve which agent handles a channel.

        Profile channels use the format '{profile_name}:{channel_type}'.
        Non-profile channels fall back to the runtime's unified/per-session logic.
        """
        if channel_id:
            # Profile channels are named like 'myprofile:telegram'.
            # Extract the prefix before the colon and check if a dedicated
            # agent was registered for that profile name.
            colon = channel_id.find(':')
            if colon > 0:
                profile_name = channel_id[:colon]
                profile_agent = self._profile_agents.get(profile_name)
                if profile_agent is not None:
                    # This channel belongs to a profile — use its dedicated agent.
                    return profile_agent

        # Not a profile channel. Fall back to the runtime's session strategy:
        # • unified_session_enabled → everyone shares one long-running agent
        # • otherwise             → spin up a fresh agent per conversation
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

        # Nothing to cancel if there's no session, no task, or the task already finished.
        if entry is None or entry.task is None or entry.task.done():
            return

        # Signal the running task to stop.
        entry.task.cancel()
        try:
            # Wait for the task to acknowledge the cancellation.
            await entry.task
        except (asyncio.CancelledError, Exception):
            # Both CancelledError and any mid-cancel exception are expected — ignore them.
            pass

        # Send a stream-END marker so the channel knows the partial response is over
        # and can clean up its display (e.g. remove a "typing..." indicator).
        await self._bus.publish_outgoing(OutgoingMessage(
            channel=channel_id,
            chat_id=chat_id,
            stream_phase=StreamPhase.END,
        ))

        # Notify any hooks that the message was cancelled (e.g. for logging/analytics).
        await self.hooks.emit(MessageCancelEvent(channel_id=channel_id, chat_id=chat_id))

    # ── Command runner ────────────────────────────────────────────────────────

    async def _run_command(self, channel_id: str, chat_id: str, parsed) -> None:
        """Dispatch a slash command and send its printed output back to the channel.

        /new from a profile channel resets that profile's session instead of
        the REPL session, since profile agents are independent of the runtime.
        """
        # Special case: /new and /clear on a profile channel should reset
        # *that profile's* session, not the main REPL session.
        # Without this, the global /new command would clear the wrong thing.
        if parsed.name in ('new', 'clear'):
            colon = channel_id.find(':')
            if colon > 0:
                profile_name = channel_id[:colon]
                if profile_name in self._profile_agents:
                    # Reset this profile's conversation history and session file.
                    self.new_profile_session(profile_name)
                    # Tell the user the slate is clean.
                    await self._bus.publish_outgoing(OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=[TextPart('Session cleared.')],
                    ))
                    return

        # For all other commands, run the command and capture its text output.
        output = await self.dispatch_command(parsed)
        if output:
            # Send the command's output back to the user in the same channel.
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

        # Pre-check TTS enablement so the END signal can suppress the text
        # message when audio will be delivered instead.
        tts_enabled = self._effective_media_enabled('tts', agent=agent)
        tts_will_fire = tts_enabled is True

        response_parts: list[str] = []
        # Tracks the last error text seen from the engine and retry metadata.
        # Wrapped in lists/dicts for mutability inside the closure.
        _last_error: list[str] = ['']
        _retry_max: list[int] = [0]  # total attempts (max_retries + 1)
        _tool_names: dict[str, str] = {}         # id → name
        _tool_display_names: dict[str, str] = {}  # id → display_name from tool_start

        async def _on_event(event) -> None:
            """Translate agent events into OutgoingMessage bus publications for the channel."""
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
                    #
                    if m.stop_reason is None:
                        for content in m.contents:
                            chunk_text = getattr(content, 'content', '')
                            if chunk_text and getattr(content, 'type', '') == 'text':
                                response_parts.append(chunk_text)
                                await self._bus.publish_outgoing(OutgoingMessage(
                                    channel=channel_id,
                                    chat_id=chat_id,
                                    parts=[TextPart(chunk_text)],
                                    stream_phase=StreamPhase.CHUNK,
                                    metadata={'kind': 'text'},
                                ))
                    elif not response_parts:
                        for content in m.contents:
                            chunk_text = getattr(content, 'content', '')
                            if chunk_text and getattr(content, 'type', '') == 'text':
                                response_parts.append(chunk_text)
                                await self._bus.publish_outgoing(OutgoingMessage(
                                    channel=channel_id,
                                    chat_id=chat_id,
                                    parts=[TextPart(chunk_text)],
                                    stream_phase=StreamPhase.CHUNK,
                                    metadata={'kind': 'text'},
                                ))
                    meta: dict = {'origin_message_id': message_id} if message_id else {}
                    if m.stop_reason not in (None, StopReason.Stop):
                        meta['keep_typing'] = True
                    if tts_will_fire and m.stop_reason == StopReason.Stop:
                        meta['suppress_text'] = True
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
        from operator_use.bus.types import AudioPart, FilePart, ImagePart
        attachments = [
            MessageAttachment(path=getattr(p, 'audio', None) or getattr(p, 'path', ''), mime_type=getattr(p, 'mime_type', None))
            for p in (parts or [])
            if isinstance(p, (AudioPart, FilePart))
        ]
        image_paths: list[str] = [
            path
            for p in (parts or [])
            if isinstance(p, ImagePart) and p.paths
            for path in p.paths
        ]
        attachments += [
            MessageAttachment(path=path, mime_type="image/jpeg")
            for path in image_paths
        ]
        reply_to = (msg_metadata or {}).get('reply_to')
        msg_meta = MessageMeta(
            reply_to=str(reply_to) if reply_to else None,
            channel_message_id=message_id or None,
            attachments=attachments or None,
        )

        # Expose channel + chat_id + message_id via contextvars so tools (send,
        # subagent) know where to deliver results and which message to react to.
        _session_channel.set(channel_id)
        _session_chat_id.set(chat_id)
        _session_message_id.set(message_id)

        unsub = agent.hooks.subscribe(_on_event)
        try:
            await agent.invoke(text, PromptOptions(source='interactive', meta=msg_meta, channel=channel_id, images=image_paths))
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
        # tts_enabled is resolved from the active profile's settings overlay so profile-level
        # TTS toggles are respected (the hook reads from the global settings manager which
        # does not include the profile overlay).
        response_text = "".join(response_parts)
        send_results = await self.hooks.emit(MessageSendEvent(
            channel_id=channel_id,
            chat_id=chat_id,
            input_text=text,
            response_text=response_text,
            is_voice=is_voice,
            tts_enabled=tts_enabled,
        ))
        tts_audio_sent = False
        for r in send_results:
            if isinstance(r, MessageSendResult) and r.parts:
                channel = self._channels.get(channel_id)
                if channel is not None:
                    from operator_use.bus.types import AudioPart as _AudioPart
                    audio_path = next(
                        (p.audio for p in r.parts if isinstance(p, _AudioPart)), None
                    )
                    audio_out = OutgoingMessage(
                        channel=channel_id,
                        chat_id=chat_id,
                        parts=r.parts,
                        metadata={'audio_path': audio_path} if audio_path else {},
                    )
                    await channel.send(audio_out)
                    tts_audio_sent = True

                    # Patch the assistant message in the session with the audio path.
                    if audio_path:
                        entry_id = getattr(agent, '_last_assistant_entry_id', None)
                        if entry_id:
                            from operator_use.session.types import MessageMeta, MessageAttachment
                            agent._session_manager.patch_entry_meta(
                                entry_id,
                                MessageMeta(attachments=[MessageAttachment(path=audio_path, mime_type='audio/wav')]),
                            )

        # If TTS was expected but failed, fall back to sending the text directly
        # so the user still gets a response.
        if tts_will_fire and not tts_audio_sent and response_text:
            channel = self._channels.get(channel_id)
            if channel is not None:
                await channel.send(OutgoingMessage(
                    channel=channel_id,
                    chat_id=chat_id,
                    parts=[TextPart(response_text)],
                ))

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
