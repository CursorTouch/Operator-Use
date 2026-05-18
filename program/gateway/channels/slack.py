from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel, GatewayEvent

if TYPE_CHECKING:
    from program.gateway.service import Gateway

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
    One channel per Slack conversation.

    For threads, `thread_ts` pins replies to the thread.
    Accumulates text chunks and posts a single message when stream_end fires.
    """

    def __init__(
        self,
        client: AsyncWebClient,
        slack_channel_id: str,
        thread_ts: str | None = None,
    ) -> None:
        self._client = client
        self._slack_channel_id = slack_channel_id
        self._thread_ts = thread_ts
        ts_part = f":{thread_ts}" if thread_ts else ""
        self._id = f"slack:{slack_channel_id}{ts_part}"
        self._text_buffer = ""

    @property
    def channel_id(self) -> str:
        return self._id

    async def on_event(self, event: GatewayEvent) -> None:
        match event.type:
            case 'stream_start':
                self._text_buffer = ""

            case 'chunk':
                if event.data.get('kind') == 'text':
                    self._text_buffer += event.data.get('text', '')

            case 'stream_end':
                if self._text_buffer.strip():
                    await self._post(self._text_buffer)
                    self._text_buffer = ""

            case 'tool_start':
                name = event.data.get('name', '')
                await self._post(f"⚙️ `{name}`…")

            case 'tool_end':
                if event.data.get('is_error'):
                    result = str(event.data.get('result', ''))[:500]
                    await self._post(f"⚠️ `{result}`")

            case 'error':
                await self._post(f"❌ {event.data.get('message', 'Unknown error')}")

    async def _post(self, text: str) -> None:
        try:
            await self._client.chat_postMessage(
                channel=self._slack_channel_id,
                text=text,
                thread_ts=self._thread_ts,
            )
        except Exception:
            logger.exception("SlackChannel: chat_postMessage failed")


class SlackBot:
    """
    Manages a Slack bot that routes messages through the Gateway via Socket Mode.

    No public URL is required — Socket Mode uses a persistent outbound websocket.

    Responds to:
    - App mentions (@bot) in channels.
    - Direct messages to the bot.

    Replies are posted in-thread so server channels stay tidy.

    Requires: pip install "slack-bolt>=1.0"

    Slack app configuration:
    - OAuth scopes: app_mentions:read, chat:write, im:history, im:read
    - Event subscriptions: app_mention, message.im
    - Socket Mode: enabled (generates an App-Level Token with connections:write scope)

    Usage:
        bot = SlackBot(
            gateway,
            bot_token=os.environ["SLACK_BOT_TOKEN"],   # xoxb-...
            app_token=os.environ["SLACK_APP_TOKEN"],   # xapp-...
        )
        await bot.run()   # run as a background task or directly
    """

    def __init__(self, gateway: Gateway, bot_token: str, app_token: str) -> None:
        if not _SLACK_AVAILABLE:
            raise ImportError('slack-bolt>=1.0 is required for SlackBot.')
        self._gateway = gateway
        self._bot_token = bot_token
        self._app_token = app_token
        self._channels: dict[str, SlackChannel] = {}

    def _get_or_create(
        self,
        client: AsyncWebClient,
        slack_channel_id: str,
        thread_ts: str | None,
    ) -> SlackChannel:
        ts_part = f":{thread_ts}" if thread_ts else ""
        key = f"{slack_channel_id}{ts_part}"
        if key not in self._channels:
            ch = SlackChannel(client, slack_channel_id, thread_ts)
            self._channels[key] = ch
            self._gateway.register(ch)
        return self._channels[key]

    async def run(self) -> None:
        """Start Socket Mode. Runs until the asyncio task is cancelled."""
        app = AsyncApp(token=self._bot_token)

        @app.event("app_mention")
        async def handle_mention(event: dict, client: AsyncWebClient) -> None:
            text = _MENTION_RE.sub('', event.get('text', '')).strip()
            if not text:
                return
            slack_channel_id = event['channel']
            # Reply in-thread using the triggering message's ts
            thread_ts = event.get('thread_ts') or event.get('ts')
            channel = self._get_or_create(client, slack_channel_id, thread_ts)
            await self._gateway.send(channel.channel_id, text)

        @app.event("message")
        async def handle_dm(event: dict, client: AsyncWebClient) -> None:
            # Only respond to DMs; ignore bot messages and edits.
            if event.get('channel_type') != 'im':
                return
            if event.get('subtype'):
                return
            text = event.get('text', '').strip()
            if not text:
                return
            slack_channel_id = event['channel']
            channel = self._get_or_create(client, slack_channel_id, None)
            await self._gateway.send(channel.channel_id, text)

        handler = AsyncSocketModeHandler(app, self._app_token)
        await handler.start_async()
        logger.info("Slack bot started (Socket Mode)")

        try:
            await asyncio.Future()  # run until cancelled
        finally:
            await handler.close_async()
