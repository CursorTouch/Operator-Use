from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from program.gateway.types import BaseChannel
from program.gateway.channels.shutdown import quiet_library_logging
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
from program.commands.types import CommandParseResult
from program.gateway.channels.slack.utils import (
    _MEDIA_DIR, _MENTION_RE,
    is_audio_file, audio_ext_from_file, download_slack_file,
    emoji_to_slack_name, markdown_to_slack_mrkdwn, split_message,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient
    _SLACK_AVAILABLE = True
else:
    try:
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_sdk.web.async_client import AsyncWebClient
        _SLACK_AVAILABLE = True
    except ImportError:
        _SLACK_AVAILABLE = False


class SlackChannel(BaseChannel):
    """
    A single Slack bot handling all conversations via Socket Mode.

    chat_id format: "{slack_channel_id}:{thread_ts}" if in a thread,
    otherwise just "{slack_channel_id}".

    Supports text messages and audio file sharing incoming.
    Outgoing supports text and audio (TTS) via files_upload_v2.

    Requires: pip install "slack-bolt>=1.0"
    """

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        name: str = "slack",
        commands: list[tuple[str, str]] | None = None,
        command_handler: Callable[[CommandParseResult], Awaitable[str]] | None = None,
        allow_from: list[str] | None = None,
        show_tool_calls: bool = True,
        show_thinking: bool = False,
        streaming: bool = True,
        streaming_latency: float = 1.0,
    ) -> None:
        super().__init__()
        if not _SLACK_AVAILABLE:
            raise ImportError('slack-bolt>=1.0 is required for SlackChannel.')
        self._name = name
        self._bot_token = bot_token
        self._app_token = app_token
        self._commands = commands or []
        self._command_handler = command_handler
        self._allow_from = set(allow_from or [])
        self._show_tool_calls = show_tool_calls
        self._show_thinking = show_thinking
        self._streaming = streaming
        self._streaming_latency = streaming_latency
        self._is_group: dict[str, bool] = {}  # chat_id → True if channel (not DM)
        self._buffers: dict[str, str] = {}
        self._clients: dict[str, AsyncWebClient] = {}
        self._thread_ts_map: dict[str, str | None] = {}
        self._handler: AsyncSocketModeHandler | None = None
        self._live_tasks: dict[str, asyncio.Task] = {}
        self._live_ts_map: dict[str, str | None] = {}  # chat_id → posted message ts
        self._retry_ts_map: dict[str, str] = {}     # chat_id → rolling retry-status message ts
        self._tool_ts_map: dict[str, str] = {}      # chat_id → rolling tool/thinking-status message ts (shared slot)
        self._prev_tool_ts: dict[str, str] = {}     # chat_id → tool-status ts from previous failed attempt
        self._thinking_buffers: dict[str, str] = {} # chat_id → accumulated thinking text
        self._thinking_tasks: dict[str, asyncio.Task] = {}  # chat_id → debounced thinking-stream task

    @property
    def channel_id(self) -> str:
        return self._name

    async def _build_parts(self, event: dict) -> list:
        """Extract audio and text parts from a Slack event dict."""
        parts: list = []
        for file in event.get('files', []):
            if is_audio_file(file):
                url = file.get('url_private_download') or file.get('url_private', '')
                if url:
                    ext = audio_ext_from_file(file)
                    dest = _MEDIA_DIR / f"{file.get('id', 'audio')}{ext}"
                    if await download_slack_file(url, self._bot_token, dest):
                        parts.append(AudioPart(audio=str(dest), mime_type=file.get('mimetype')))
        return parts

    async def connect(self) -> None:
        """Create AsyncApp, register handlers, start SocketModeHandler. Runs until cancelled."""
        app = AsyncApp(token=self._bot_token)
        self._register_slash_commands(app)

        @app.event("app_mention")
        async def handle_mention(event: dict, client: AsyncWebClient) -> None:
            user_id = event.get('user', '')
            if self._allow_from and user_id not in self._allow_from:
                return
            slack_channel_id = event['channel']
            thread_ts = event.get('thread_ts') or event.get('ts')
            chat_id = f"{slack_channel_id}:{thread_ts}" if thread_ts else slack_channel_id
            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = thread_ts
            self._is_group[chat_id] = True  # app_mention always fires in a channel

            parts = await self._build_parts(event)
            text = _MENTION_RE.sub('', event.get('text', '')).strip()
            if text:
                parts.append(TextPart(text))

            if not parts:
                return

            await self.receive(IncomingMessage(
                channel=self._name,
                chat_id=chat_id,
                parts=parts,
                user_id=event.get('user', ''),
                message_id=event.get('ts', ''),
            ))

        @app.event("message")
        async def handle_dm(event: dict, client: AsyncWebClient) -> None:
            if event.get('channel_type') != 'im':
                return
            subtype = event.get('subtype')
            if subtype and subtype != 'file_share':
                return
            user_id = event.get('user', '')
            if self._allow_from and user_id not in self._allow_from:
                return

            slack_channel_id = event['channel']
            chat_id = slack_channel_id
            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = None
            self._is_group[chat_id] = False  # 'im' channel_type means DM

            parts = await self._build_parts(event)
            text = event.get('text', '').strip()
            if text:
                parts.append(TextPart(text))

            if not parts:
                return

            await self.receive(IncomingMessage(
                channel=self._name,
                chat_id=chat_id,
                parts=parts,
                user_id=event.get('user', ''),
                message_id=event.get('ts', ''),
            ))

        self._handler = AsyncSocketModeHandler(app, self._app_token)
        assert self._handler is not None
        await self._handler.start_async()
        logger.info("Slack bot started (Socket Mode)")

        try:
            await asyncio.Future()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

    _COMMAND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

    def _valid_commands(self) -> list[str]:
        return [
            name
            for name, _desc in self._commands
            if self._COMMAND_NAME_RE.fullmatch(name)
        ]

    def _register_slash_commands(self, app) -> None:
        if self._command_handler is None:
            return

        for name in self._valid_commands():
            def _make_handler(command_name: str):
                async def _handle_command(ack, respond, command) -> None:
                    await ack()

                    user_id = command.get("user_id", "")
                    if self._allow_from and user_id not in self._allow_from:
                        await respond("You are not allowed to use this bot.")
                        return

                    raw_args = command.get("text", "").strip()
                    parsed = CommandParseResult(
                        name=command_name,
                        args=raw_args.split() if raw_args else [],
                        raw=f"/{command_name} {raw_args}".strip(),
                    )
                    try:
                        output = await self._command_handler(parsed) if self._command_handler is not None else ""
                    except Exception:
                        logger.exception("SlackChannel: slash command failed: /%s", command_name)
                        output = f"Command failed: /{command_name}"

                    if output:
                        for chunk in split_message(markdown_to_slack_mrkdwn(output)):
                            await respond(chunk)

                return _handle_command

            app.command(f"/{name}")(_make_handler(name))

    async def disconnect(self) -> None:
        """Close the Socket Mode handler."""
        if self._handler is not None:
            with quiet_library_logging("slack_bolt", "slack_sdk", "aiohttp", "asyncio") as debug:
                try:
                    await self._handler.close_async()
                except Exception:
                    if debug:
                        logger.exception("SlackChannel: error during disconnect")
            self._handler = None

    def _start_live_streaming(self, chat_id: str, slack_channel_id: str, thread_ts: str | None, client) -> None:
        self._stop_live_streaming(chat_id)
        self._live_ts_map[chat_id] = None
        latency = self._streaming_latency

        async def _loop() -> None:
            await asyncio.sleep(latency)
            while True:
                buffered = self._buffers.get(chat_id, "")
                if buffered.strip() and client is not None:
                    existing_ts = self._live_ts_map.get(chat_id)
                    if existing_ts is None:
                        try:
                            resp = await client.chat_postMessage(
                                channel=slack_channel_id,
                                text=markdown_to_slack_mrkdwn(buffered),
                                thread_ts=thread_ts,
                            )
                            self._live_ts_map[chat_id] = resp.get("ts")
                        except Exception:
                            logger.exception("SlackChannel: live chat_postMessage failed")
                    else:
                        try:
                            await client.chat_update(
                                channel=slack_channel_id,
                                ts=existing_ts,
                                text=markdown_to_slack_mrkdwn(buffered),
                            )
                        except Exception:
                            pass  # edit conflicts during rapid chunks are benign
                await asyncio.sleep(latency)

        self._live_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_live_streaming(self, chat_id: str) -> None:
        task = self._live_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    _THINKING_MAX_CHARS = 800

    def _start_thinking_stream(self, chat_id: str, slack_channel_id: str, thread_ts: str | None, client) -> None:
        self._stop_thinking_stream(chat_id)
        latency = self._streaming_latency

        async def _loop() -> None:
            await asyncio.sleep(latency)
            while True:
                buffered = self._thinking_buffers.get(chat_id, "")
                if buffered.strip() and client is not None:
                    display = buffered[:self._THINKING_MAX_CHARS]
                    if len(buffered) > self._THINKING_MAX_CHARS:
                        display += "…"
                    label = f"💭 {display}"
                    existing_ts = self._tool_ts_map.get(chat_id)
                    if existing_ts is not None:
                        try:
                            await client.chat_update(channel=slack_channel_id, ts=existing_ts, text=label)
                        except Exception:
                            pass
                    else:
                        try:
                            resp = await client.chat_postMessage(channel=slack_channel_id, text=label, thread_ts=thread_ts)
                            if resp.get('ok') and resp.get('ts'):
                                self._tool_ts_map[chat_id] = resp['ts']
                        except Exception:
                            logger.exception("SlackChannel: chat_postMessage failed (thinking)")
                await asyncio.sleep(latency)

        self._thinking_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_thinking_stream(self, chat_id: str) -> None:
        task = self._thinking_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to Slack."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        parts = chat_id.split(":", 1)
        slack_channel_id = parts[0]
        thread_ts = parts[1] if len(parts) > 1 else self._thread_ts_map.get(chat_id)
        # Auto-reply in channels only — DMs post to the root, no threading needed.
        if not self._is_group.get(chat_id, False):
            thread_ts = None

        client = self._clients.get(chat_id)

        async def _post(text: str) -> None:
            if client is None:
                logger.warning("SlackChannel: no client for chat_id %r", chat_id)
                return
            try:
                await client.chat_postMessage(
                    channel=slack_channel_id,
                    text=text,
                    thread_ts=thread_ts,
                )
            except Exception:
                logger.exception("SlackChannel: chat_postMessage failed")

        if phase == StreamPhase.START:
            self._buffers[chat_id] = ""
            self._thinking_buffers.pop(chat_id, None)
            self._stop_thinking_stream(chat_id)
            stale = self._tool_ts_map.pop(chat_id, None)
            if stale is not None:
                self._prev_tool_ts[chat_id] = stale
            if self._streaming:
                self._start_live_streaming(chat_id, slack_channel_id, thread_ts, client)

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'thinking':
                if not self._show_thinking:
                    return
                chunk = text_from_parts(msg.parts)
                self._thinking_buffers[chat_id] = self._thinking_buffers.get(chat_id, "") + chunk
                if chat_id not in self._thinking_tasks:
                    self._start_thinking_stream(chat_id, slack_channel_id, thread_ts, client)
            elif kind == 'tool_start' and self._show_tool_calls:
                self._stop_thinking_stream(chat_id)
                self._thinking_buffers.pop(chat_id, None)
                name = metadata.get('name', '')
                label = f"⚙️ `{name}`…"
                existing_ts = self._tool_ts_map.get(chat_id)
                if existing_ts is not None and client is not None:
                    try:
                        await client.chat_update(channel=slack_channel_id, ts=existing_ts, text=label)
                    except Exception:
                        if client is not None:
                            try:
                                resp = await client.chat_postMessage(channel=slack_channel_id, text=label, thread_ts=thread_ts)
                                if resp.get('ok') and resp.get('ts'):
                                    self._tool_ts_map[chat_id] = resp['ts']
                            except Exception:
                                logger.exception("SlackChannel: chat_postMessage failed (tool_start)")
                elif client is not None:
                    try:
                        resp = await client.chat_postMessage(channel=slack_channel_id, text=label, thread_ts=thread_ts)
                        if resp.get('ok') and resp.get('ts'):
                            self._tool_ts_map[chat_id] = resp['ts']
                    except Exception:
                        logger.exception("SlackChannel: chat_postMessage failed (tool_start)")
            elif kind == 'tool_update' and self._show_tool_calls:
                text = metadata.get('text', '')
                existing_ts = self._tool_ts_map.get(chat_id)
                if text and existing_ts is not None and client is not None:
                    try:
                        await client.chat_update(channel=slack_channel_id, ts=existing_ts, text=text)
                    except Exception:
                        pass
            elif kind == 'tool_end' and self._show_tool_calls:
                name = metadata.get('name', '')
                is_error = metadata.get('is_error', False)
                if is_error:
                    result = str(metadata.get('result', ''))
                    label = f"❌ `{name}`\n{result}" if result else f"❌ `{name}`"
                else:
                    label = f"✅ `{name}`"
                existing_ts = self._tool_ts_map.get(chat_id)
                if existing_ts is not None and client is not None:
                    try:
                        await client.chat_update(channel=slack_channel_id, ts=existing_ts, text=label)
                    except Exception:
                        pass
            else:
                # text chunk — stop thinking stream and delete the rolling status message.
                self._stop_thinking_stream(chat_id)
                self._thinking_buffers.pop(chat_id, None)
                existing_ts = self._tool_ts_map.pop(chat_id, None)
                if existing_ts is not None and client is not None:
                    try:
                        await client.chat_delete(channel=slack_channel_id, ts=existing_ts)
                    except Exception:
                        pass
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            self._stop_thinking_stream(chat_id)
            self._thinking_buffers.pop(chat_id, None)
            buffered = self._buffers.pop(chat_id, "")
            # keep_typing leaves the live-streaming loop running so subsequent
            # turns (e.g. after a tool call) stream into a new live message.
            keep_typing = metadata.get('keep_typing', False)
            # Final END — sweep any leftover tool-status message.
            if not keep_typing:
                leftover = self._tool_ts_map.pop(chat_id, None)
                if leftover is not None and client is not None:
                    try:
                        await client.chat_delete(channel=slack_channel_id, ts=leftover)
                    except Exception:
                        pass
            if self._streaming:
                if not keep_typing:
                    self._stop_live_streaming(chat_id)
                live_ts = self._live_ts_map.pop(chat_id, None)
                if buffered.strip():
                    if live_ts is not None and client is not None:
                        try:
                            await client.chat_update(
                                channel=slack_channel_id,
                                ts=live_ts,
                                text=markdown_to_slack_mrkdwn(buffered),
                            )
                        except Exception:
                            logger.exception("SlackChannel: chat_update failed (end)")
                    else:
                        for chunk in split_message(buffered):
                            await _post(markdown_to_slack_mrkdwn(chunk))
                if keep_typing:
                    self._buffers[chat_id] = ""
            else:
                if buffered.strip():
                    for chunk in split_message(buffered):
                        await _post(markdown_to_slack_mrkdwn(chunk))

        elif phase == StreamPhase.ERROR:
            retry_flag = metadata.get('retry', False)
            if retry_flag:
                existing_ts = self._retry_ts_map.get(chat_id)
                if metadata.get('retry_success'):
                    if existing_ts is not None and client is not None:
                        try:
                            await client.chat_delete(channel=slack_channel_id, ts=existing_ts)
                        except Exception:
                            pass
                        self._retry_ts_map.pop(chat_id, None)
                    prev_tool_ts = self._prev_tool_ts.pop(chat_id, None)
                    if prev_tool_ts is not None and client is not None:
                        try:
                            await client.chat_delete(channel=slack_channel_id, ts=prev_tool_ts)
                        except Exception:
                            pass
                    return
                text = text_from_parts(msg.parts) or 'Unknown error'
                attempt = metadata.get('retry_attempt', 1)
                total = metadata.get('retry_max', 1)
                is_final = metadata.get('retry_final', False)
                if is_final:
                    label = f"❌ {text}" if total <= 1 else f"❌ {text}\n(failed after {total} attempt{'s' if total != 1 else ''})"
                else:
                    label = f"❌ {text}\n⏳ Retrying… ({attempt}/{total})"
                if existing_ts is not None and client is not None:
                    try:
                        await client.chat_update(channel=slack_channel_id, ts=existing_ts, text=label)
                    except Exception:
                        await _post(label)
                else:
                    if client is not None:
                        try:
                            resp = await client.chat_postMessage(channel=slack_channel_id, text=label, thread_ts=thread_ts)
                            if resp.get('ok') and resp.get('ts'):
                                self._retry_ts_map[chat_id] = resp['ts']
                        except Exception:
                            logger.exception("SlackChannel: chat_postMessage failed (retry error)")
                if is_final:
                    self._retry_ts_map.pop(chat_id, None)
            else:
                await _post(f"❌ {text_from_parts(msg.parts) or 'Unknown error'}")

        elif phase is None:
            kind = metadata.get('kind')
            if kind == 'react':
                emoji = emoji_to_slack_name(metadata.get('emoji', 'thumbsup'))
                message_id = metadata.get('message_id', '')
                if client is not None and message_id:
                    try:
                        await client.reactions_add(
                            channel=slack_channel_id,
                            name=emoji,
                            timestamp=message_id,
                        )
                    except Exception:
                        logger.exception("SlackChannel: reactions_add failed for %r", chat_id)
                return

            # When reply=True the send tool passes reply_to=<original message ts>
            reply_thread_ts = metadata.get('reply_to') or thread_ts

            for p in msg.parts:
                match p:
                    case AudioPart(audio=audio):
                        try:
                            if client is not None:
                                with open(audio, 'rb') as f:
                                    await client.files_upload_v2(
                                        channel=slack_channel_id,
                                        file=f.read(),
                                        filename=Path(audio).name,
                                        thread_ts=reply_thread_ts,
                                    )
                        except Exception:
                            logger.exception("SlackChannel: send audio failed for %r", audio)
                    case FilePart(path=fp):
                        try:
                            if client is not None:
                                caption = text_from_parts(msg.parts) or None
                                with open(fp, 'rb') as f:
                                    await client.files_upload_v2(
                                        channel=slack_channel_id,
                                        file=f.read(),
                                        filename=Path(fp).name,
                                        initial_comment=caption,
                                        thread_ts=reply_thread_ts,
                                    )
                        except Exception:
                            logger.exception("SlackChannel: send_file failed for %r", fp)
                        return  # caption already sent with the file
            text = text_from_parts(msg.parts)
            if text:
                if client is not None:
                    for chunk in split_message(text):
                        try:
                            await client.chat_postMessage(
                                channel=slack_channel_id,
                                text=markdown_to_slack_mrkdwn(chunk),
                                thread_ts=reply_thread_ts,
                            )
                        except Exception:
                            logger.exception("SlackChannel: chat_postMessage failed")


SlackBot = SlackChannel
