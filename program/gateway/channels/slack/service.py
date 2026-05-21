from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel
from program.gateway.channels.shutdown import quiet_library_logging
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
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
        allow_from: list[str] | None = None,
        show_tool_calls: bool = True,
        streaming: bool = True,
        streaming_latency: float = 1.0,
    ) -> None:
        super().__init__()
        if not _SLACK_AVAILABLE:
            raise ImportError('slack-bolt>=1.0 is required for SlackChannel.')
        self._bot_token = bot_token
        self._app_token = app_token
        self._allow_from = set(allow_from or [])
        self._show_tool_calls = show_tool_calls
        self._streaming = streaming
        self._streaming_latency = streaming_latency
        self._is_group: dict[str, bool] = {}  # chat_id → True if channel (not DM)
        self._buffers: dict[str, str] = {}
        self._clients: dict[str, AsyncWebClient] = {}
        self._thread_ts_map: dict[str, str | None] = {}
        self._handler: AsyncSocketModeHandler | None = None
        self._live_tasks: dict[str, asyncio.Task] = {}
        self._live_ts_map: dict[str, str | None] = {}  # chat_id → posted message ts
        self._retry_ts_map: dict[str, str] = {}  # chat_id → rolling retry-status message ts

    @property
    def channel_id(self) -> str:
        return "slack"

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
                channel="slack",
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
                channel="slack",
                chat_id=chat_id,
                parts=parts,
                user_id=event.get('user', ''),
                message_id=event.get('ts', ''),
            ))

        self._handler = AsyncSocketModeHandler(app, self._app_token)
        await self._handler.start_async()
        logger.info("Slack bot started (Socket Mode)")

        try:
            await asyncio.Future()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

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
            if self._streaming:
                self._start_live_streaming(chat_id, slack_channel_id, thread_ts, client)

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start' and self._show_tool_calls:
                await _post(f"⚙️ `{metadata.get('name', '')}`…")
            elif kind == 'tool_end' and metadata.get('is_error') and self._show_tool_calls:
                await _post(f"⚠️ {metadata.get('result', '')}")
            else:
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            # keep_typing leaves the live-streaming loop running so subsequent
            # turns (e.g. after a tool call) stream into a new live message.
            keep_typing = metadata.get('keep_typing', False)
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
