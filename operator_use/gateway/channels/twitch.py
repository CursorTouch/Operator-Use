"""Twitch channel using twitchio."""

import asyncio
import logging
from typing import Optional

import twitchio
from twitchio.ext import commands

from operator_use.bus.views import (
    IncomingMessage,
    OutgoingMessage,
    StreamPhase,
    TextPart,
    text_from_parts,
)
from operator_use.gateway.channels.config import TwitchConfig
from operator_use.gateway.channels.base import BaseChannel

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 500  # Twitch chat message character limit


def _split_message(content: str, max_len: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split content into chunks within max_len, preferring line breaks."""
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


class _TwitchBot(commands.Bot):
    """twitchio Bot subclass that routes messages to a TwitchChannel."""

    def __init__(
        self, channel: "TwitchChannel", token: str, nick: str, channel_name: str, prefix: str
    ) -> None:
        # twitchio expects token without the "oauth:" prefix in some versions
        super().__init__(token=token, nick=nick, prefix=prefix, initial_channels=[channel_name])
        self._operator_channel = channel

    async def event_ready(self) -> None:
        logger.info(f"Twitch bot connected as {self.nick}")

    async def event_message(self, message: twitchio.Message) -> None:
        # Ignore messages sent by the bot itself
        if message.echo:
            return
        await self._operator_channel._on_message(message)

    async def event_error(self, error: Exception, data: str = "") -> None:
        logger.error(f"Twitch bot error: {error}", exc_info=True)


class TwitchChannel(BaseChannel):
    """Twitch chat channel using twitchio."""

    def __init__(self, config: "TwitchConfig", bus=None) -> None:
        super().__init__(config, bus)
        self._bot: Optional[_TwitchBot] = None
        # stream_state: chat_id -> accumulated text buffer
        self._stream_buffer: dict[str, str] = {}

    # _cfg inherited from BaseChannel

    @property
    def name(self) -> str:
        return "twitch"

    async def start(self) -> None:
        """Start the Twitch bot."""
        token = self._cfg("token") or ""
        nick = self._cfg("nick") or ""
        channel_name = self._cfg("channel_name") or ""

        if not token or not nick or not channel_name:
            logger.warning("Twitch channel: token, nick, and channel_name are all required")
            return

        try:
            await self._listen()
        except Exception as e:
            logger.error(f"Failed to start Twitch bot: {e}", exc_info=True)

    async def stop(self) -> None:
        """Disconnect the Twitch bot."""
        self._stream_buffer.clear()
        if self._bot:
            try:
                await self._bot.close()
            except Exception as e:
                logger.debug(f"Twitch bot shutdown: {e}")
            self._bot = None

    async def _listen(self) -> None:
        """Connect to Twitch IRC and listen for messages."""
        token = self._cfg("token") or ""
        nick = self._cfg("nick") or ""
        channel_name = self._cfg("channel_name") or ""
        prefix = self._cfg("prefix") or "!"

        self._bot = _TwitchBot(
            channel=self,
            token=token,
            nick=nick,
            channel_name=channel_name,
            prefix=prefix,
        )

        try:
            logger.info(f"Twitch bot starting (channel: #{channel_name})...")
            await self._bot.start()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Twitch bot error: {e}", exc_info=True)

    async def _on_message(self, message: twitchio.Message) -> None:
        """Handle an incoming Twitch chat message."""
        channel_name = self._cfg("channel_name") or ""
        author_name = message.author.name if message.author else ""
        if not self._is_user_allowed(author_name):
            return

        content = message.content or ""
        if not content:
            return

        chat_id = channel_name  # use channel name as chat_id (Twitch has one chat per channel)

        # Session control commands always use / prefix (e.g. /start, /stop, /restart)
        _parts = content.strip().split(maxsplit=1)
        _cmd_word = _parts[0].lower() if _parts else ""
        for cmd in ("start", "stop", "restart"):
            if _cmd_word == f"/{cmd}":
                command_args = _parts[1] if len(_parts) > 1 else ""
                incoming = IncomingMessage(
                    channel=self.name,
                    chat_id=chat_id,
                    parts=[TextPart(content=content.strip())],
                    user_id=author_name,
                    account_id=self._cfg("account_id") or "",
                    metadata={
                        "username": author_name,
                        "channel": channel_name,
                        "_command": cmd,
                        "_command_args": command_args,
                    },
                )
                await self.receive(incoming)
                return

        incoming = IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            parts=[TextPart(content=content)],
            user_id=author_name,
            account_id=self._cfg("account_id") or "",
            metadata={
                "username": author_name,
                "channel": channel_name,
            },
        )
        await self.receive(incoming)

    async def send(self, message: OutgoingMessage) -> int | None:
        """Send an outgoing message to Twitch chat."""
        if not self._bot:
            return None

        channel_name = self._cfg("channel_name") or ""
        chat_id = message.chat_id or channel_name
        phase = message.stream_phase

        # Streaming: buffer chunks and send only when complete
        if phase in (StreamPhase.START, StreamPhase.CHUNK):
            content = text_from_parts(message.parts) or ""
            self._stream_buffer[chat_id] = content
            return None

        if phase == StreamPhase.END:
            content = text_from_parts(message.parts) or self._stream_buffer.pop(chat_id, "")
            self._stream_buffer.pop(chat_id, None)
            await self._send_text(channel_name, content)
            return None

        if phase == StreamPhase.DONE:
            self._stream_buffer.pop(chat_id, None)
            return None

        # Non-streaming: send immediately
        content = text_from_parts(message.parts) or ""
        if content:
            await self._send_text(channel_name, content)

        return None

    async def _send_text(self, channel_name: str, content: str) -> None:
        """Send text to a Twitch channel, splitting if needed."""
        if not content or not self._bot:
            return

        twitch_channel = self._bot.get_channel(channel_name)
        if not twitch_channel:
            logger.warning(f"Twitch channel '{channel_name}' not found in bot")
            return

        for chunk in _split_message(content):
            try:
                await twitch_channel.send(chunk)
            except Exception as e:
                logger.error(f"Twitch send error: {e}")
