from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel, GatewayEvent

if TYPE_CHECKING:
    from program.gateway.service import Gateway

logger = logging.getLogger(__name__)

try:
    import discord
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False

_DISCORD_MSG_LIMIT = 2000


class DiscordChannel(BaseChannel):
    """
    One channel per Discord text channel or DM.

    Accumulates text chunks and sends a message when stream_end fires.
    Tool events post brief status messages.
    """

    def __init__(self, channel: discord.abc.Messageable, channel_id: int) -> None:
        self._channel = channel
        self._discord_id = channel_id
        self._id = f"discord:{channel_id}"
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
                    await self.send(self._text_buffer)
                    self._text_buffer = ""

            case 'tool_start':
                name = event.data.get('name', '')
                await self._channel.send(f"⚙️ `{name}`…")

            case 'tool_end':
                if event.data.get('is_error'):
                    result = str(event.data.get('result', ''))[:300]
                    await self._channel.send(f"⚠️ `{result}`")

            case 'error':
                await self.send(f"❌ {event.data.get('message', 'Unknown error')}")

    async def send(self, text: str) -> None:
        for i in range(0, len(text), _DISCORD_MSG_LIMIT):
            await self._channel.send(text[i:i + _DISCORD_MSG_LIMIT])


class DiscordBot:
    """
    A Discord bot that routes messages through the Gateway.

    Responds to:
    - DMs: every text message.
    - Servers: only messages that @mention the bot.

    Requires: pip install "discord.py>=2.0"
    Requires intents: message_content=True  (enable in Discord Developer Portal).

    Usage:
        bot = DiscordBot(gateway, token=os.environ["DISCORD_BOT_TOKEN"])
        await bot.run()   # run as a background task or directly
    """

    def __init__(self, gateway: Gateway, token: str) -> None:
        if not _DISCORD_AVAILABLE:
            raise ImportError('discord.py>=2.0 is required for DiscordBot.')
        self._gateway = gateway
        self._token = token
        self._channels: dict[int, DiscordChannel] = {}

    def _get_or_create(
        self,
        discord_channel: discord.abc.Messageable,
        channel_id: int,
    ) -> DiscordChannel:
        if channel_id not in self._channels:
            ch = DiscordChannel(discord_channel, channel_id)
            self._channels[channel_id] = ch
            self._gateway.register(ch)
        return self._channels[channel_id]

    async def start(self) -> None:
        """Connect and serve. Runs until the asyncio task is cancelled."""
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            logger.info("Discord bot logged in as %s", client.user)

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mentioned = client.user in message.mentions if client.user else False
            if not is_dm and not is_mentioned:
                return

            text = message.content
            if client.user:
                text = text.replace(f'<@{client.user.id}>', '').strip()
            if not text:
                return

            channel = self._get_or_create(message.channel, message.channel.id)
            async with message.channel.typing():
                await self._gateway.send(channel.channel_id, text)

        await client.start(self._token)
