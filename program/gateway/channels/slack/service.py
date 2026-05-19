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
    emoji_to_slack_name,
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

    def __init__(self, bot_token: str, app_token: str) -> None:
        super().__init__()
        if not _SLACK_AVAILABLE:
            raise ImportError('slack-bolt>=1.0 is required for SlackChannel.')
        self._bot_token = bot_token
        self._app_token = app_token
        self._buffers: dict[str, str] = {}
        self._clients: dict[str, AsyncWebClient] = {}
        self._thread_ts_map: dict[str, str | None] = {}
        self._handler: AsyncSocketModeHandler | None = None

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
            slack_channel_id = event['channel']
            thread_ts = event.get('thread_ts') or event.get('ts')
            chat_id = f"{slack_channel_id}:{thread_ts}" if thread_ts else slack_channel_id
            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = thread_ts

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

            slack_channel_id = event['channel']
            chat_id = slack_channel_id
            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = None

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

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to Slack."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        parts = chat_id.split(":", 1)
        slack_channel_id = parts[0]
        thread_ts = parts[1] if len(parts) > 1 else self._thread_ts_map.get(chat_id)

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

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start':
                await _post(f"⚙️ `{metadata.get('name', '')}`…")
            elif kind == 'tool_end' and metadata.get('is_error'):
                await _post(f"⚠️ {metadata.get('result', '')}")
            else:
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            if buffered.strip():
                await _post(buffered)

        elif phase == StreamPhase.ERROR:
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
                    try:
                        await client.chat_postMessage(
                            channel=slack_channel_id,
                            text=text,
                            thread_ts=reply_thread_ts,
                        )
                    except Exception:
                        logger.exception("SlackChannel: chat_postMessage failed")


SlackBot = SlackChannel
