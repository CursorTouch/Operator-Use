from __future__ import annotations

import asyncio
import logging
import re

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, text_from_parts

logger = logging.getLogger(__name__)

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False

_MENTION_RE = re.compile(r'<@[A-Z0-9]+>')


class SlackChannel(BaseChannel):
    """
    A single Slack bot handling all conversations via Socket Mode.

    chat_id format: "{slack_channel_id}:{thread_ts}" if in a thread,
    otherwise just "{slack_channel_id}".

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

    async def connect(self) -> None:
        """Create AsyncApp, register handlers, start SocketModeHandler. Runs until cancelled."""
        app = AsyncApp(token=self._bot_token)

        @app.event("app_mention")
        async def handle_mention(event: dict, client: AsyncWebClient) -> None:
            text = _MENTION_RE.sub('', event.get('text', '')).strip()
            if not text:
                return
            slack_channel_id = event['channel']
            thread_ts = event.get('thread_ts') or event.get('ts')
            chat_id = f"{slack_channel_id}:{thread_ts}" if thread_ts else slack_channel_id

            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = thread_ts

            user_id = event.get('user', '')
            incoming = IncomingMessage(
                channel="slack",
                chat_id=chat_id,
                parts=[TextPart(text)],
                user_id=user_id,
            )
            await self.receive(incoming)

        @app.event("message")
        async def handle_dm(event: dict, client: AsyncWebClient) -> None:
            if event.get('channel_type') != 'im':
                return
            if event.get('subtype'):
                return
            text = event.get('text', '').strip()
            if not text:
                return
            slack_channel_id = event['channel']
            chat_id = slack_channel_id

            self._clients[chat_id] = client
            self._thread_ts_map[chat_id] = None

            user_id = event.get('user', '')
            incoming = IncomingMessage(
                channel="slack",
                chat_id=chat_id,
                parts=[TextPart(text)],
                user_id=user_id,
            )
            await self.receive(incoming)

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
            try:
                await self._handler.close_async()
            except Exception:
                logger.exception("SlackChannel: error during disconnect")
            self._handler = None

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to Slack."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        # Extract slack_channel_id and thread_ts from chat_id
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
                name = metadata.get('name', '')
                await _post(f"⚙️ `{name}`…")
            elif kind == 'tool_end' and metadata.get('is_error'):
                result = str(metadata.get('result', ''))
                await _post(f"⚠️ `{result}`")
            else:
                # text or thinking chunk — accumulate
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            if buffered.strip():
                await _post(buffered)

        elif phase == StreamPhase.ERROR:
            text = text_from_parts(msg.parts) or "Unknown error"
            await _post(f"❌ {text}")

        elif phase is None:
            # Direct send (out-of-band)
            text = text_from_parts(msg.parts)
            if text:
                await _post(text)


# Backward compatibility alias
SlackBot = SlackChannel
