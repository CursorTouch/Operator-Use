from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel, GatewayEvent

if TYPE_CHECKING:
    from program.gateway.service import Gateway

logger = logging.getLogger(__name__)

try:
    import twitchio
    from twitchio.ext import commands as twitch_commands
    _TWITCHIO_AVAILABLE = True
except ImportError:
    _TWITCHIO_AVAILABLE = False

_TWITCH_MSG_LIMIT = 500


def _split(text: str, limit: int = _TWITCH_MSG_LIMIT) -> list[str]:
    """Split text into chunks within limit, breaking on newlines then spaces."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text[:limit]
        pos = cut.rfind('\n')
        if pos <= 0:
            pos = cut.rfind(' ')
        if pos <= 0:
            pos = limit
        chunks.append(text[:pos])
        text = text[pos:].lstrip()
    return chunks


class TwitchChannel(BaseChannel):
    """
    One channel for the entire Twitch chat room.

    start() connects to Twitch IRC and runs until cancelled.
    Accumulates text chunks and sends the full response when stream_end fires,
    splitting into ≤500 character messages as required by Twitch.

    Requires: pip install "twitchio>=2.0"

    Twitch developer setup:
    - Create an app at dev.twitch.tv
    - Generate an OAuth token with `chat:read` and `chat:edit` scopes
    - Token format: "oauth:xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    Usage:
        ch = TwitchChannel(
            gateway,
            token="oauth:...",
            nick="my_bot_name",
            channel_name="my_channel",
            allow_from=["trusted_user1"],
        )
        gateway.register(ch)
        await ch.start()
    """

    def __init__(
        self,
        gateway: Gateway,
        token: str,
        nick: str,
        channel_name: str,
        prefix: str = "!",
        allow_from: list[str] | None = None,
    ) -> None:
        if not _TWITCHIO_AVAILABLE:
            raise ImportError('twitchio>=2.0 is required for TwitchChannel.')
        self._gateway = gateway
        self._token = token
        self._nick = nick
        self._twitch_channel_name = channel_name.lstrip('#')
        self._prefix = prefix
        self._allow_from = [u.lower() for u in (allow_from or [])]
        self._id = f"twitch:{self._twitch_channel_name}"
        self._text_buffer = ""
        self._bot_ref: _TwitchBotImpl | None = None

    @property
    def channel_id(self) -> str:
        return self._id

    async def start(self) -> None:
        """Connect to Twitch IRC and serve. Runs until the asyncio task is cancelled."""
        bot = _TwitchBotImpl(
            channel=self,
            gateway=self._gateway,
            token=self._token,
            nick=self._nick,
            channel_name=self._twitch_channel_name,
            prefix=self._prefix,
            allow_from=self._allow_from,
        )
        self._bot_ref = bot
        try:
            await bot.start()
        except asyncio.CancelledError:
            pass
        finally:
            self._gateway.unregister(self._id)
            try:
                await bot.close()
            except Exception:
                pass

    async def on_event(self, event: GatewayEvent) -> None:
        match event.type:
            case 'stream_start':
                self._text_buffer = ""

            case 'chunk':
                if event.data.get('kind') == 'text':
                    self._text_buffer += event.data.get('text', '')

            case 'stream_end':
                if self._text_buffer.strip():
                    await self.send(self._text_buffer)
                    self._text_buffer = ""

            case 'tool_start':
                name = event.data.get('name', '')
                await self.send(f"⚙️ {name}…")

            case 'error':
                msg = event.data.get('message', 'Unknown error')
                await self.send(f"❌ {msg}")

    async def send(self, text: str) -> None:
        if not self._bot_ref:
            return
        twitch_ch = self._bot_ref.get_channel(self._twitch_channel_name)
        if not twitch_ch:
            logger.warning("TwitchChannel: channel %r not found in bot", self._twitch_channel_name)
            return
        for chunk in _split(text):
            try:
                await twitch_ch.send(chunk)
            except Exception:
                logger.exception("TwitchChannel: send failed")


class _TwitchBotImpl(twitch_commands.Bot if _TWITCHIO_AVAILABLE else object):
    """Internal twitchio Bot that routes messages to a TwitchChannel."""

    def __init__(
        self,
        channel: TwitchChannel,
        gateway: Gateway,
        token: str,
        nick: str,
        channel_name: str,
        prefix: str,
        allow_from: list[str],
    ) -> None:
        super().__init__(
            token=token,
            nick=nick,
            prefix=prefix,
            initial_channels=[channel_name],
        )
        self._operator_channel = channel
        self._gateway = gateway
        self._allow_from = allow_from

    async def event_ready(self) -> None:
        logger.info("Twitch bot connected as %s", self.nick)

    async def event_message(self, message: twitchio.Message) -> None:
        if message.echo:
            return
        author = message.author.name if message.author else ""
        if self._allow_from and author not in self._allow_from:
            return
        text = (message.content or "").strip()
        if not text:
            return
        await self._gateway.send(self._operator_channel.channel_id, text)

    async def event_error(self, error: Exception, data: str = "") -> None:
        logger.error("Twitch bot error: %s", error, exc_info=True)
