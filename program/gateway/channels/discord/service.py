from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, text_from_parts
from program.gateway.channels.discord.utils import _DISCORD_MSG_LIMIT, _MEDIA_DIR, is_audio_attachment

logger = logging.getLogger(__name__)

try:
    import discord
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False


class DiscordChannel(BaseChannel):
    """
    A single Discord bot that handles all channels/DMs.

    Supports text messages and audio file attachments incoming.
    Outgoing supports text and audio (TTS) via direct send.

    Requires: pip install "discord.py>=2.0"
    Requires intents: message_content=True (enable in Discord Developer Portal).
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        if not _DISCORD_AVAILABLE:
            raise ImportError('discord.py>=2.0 is required for DiscordChannel.')
        self._token = token
        self._buffers: dict[str, str] = {}
        self._client: discord.Client | None = None
        self._discord_channels: dict[str, discord.abc.Messageable] = {}

    @property
    def channel_id(self) -> str:
        return "discord"

    async def connect(self) -> None:
        """Create Discord client, register events, connect. Runs until cancelled."""
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        client = self._client

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

            chat_id = str(message.channel.id)
            self._discord_channels[chat_id] = message.channel
            user_id = str(message.author.id)

            parts: list = []

            for attachment in message.attachments:
                if is_audio_attachment(attachment):
                    try:
                        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                        path = _MEDIA_DIR / attachment.filename
                        await attachment.save(str(path))
                        mime = getattr(attachment, 'content_type', None) or 'audio/mpeg'
                        parts.append(AudioPart(audio=str(path), mime_type=mime))
                    except Exception:
                        logger.exception("DiscordChannel: failed to download audio attachment %r", attachment.filename)

            text = message.content
            if client.user:
                text = text.replace(f'<@{client.user.id}>', '').strip()
            if text:
                parts.append(TextPart(text))

            if not parts:
                return

            await self.receive(IncomingMessage(
                channel="discord",
                chat_id=chat_id,
                parts=parts,
                user_id=user_id,
            ))

        try:
            await client.start(self._token)
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        """Close the Discord client."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("DiscordChannel: error during disconnect")
            self._client = None

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to the Discord channel."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata
        discord_ch = self._discord_channels.get(chat_id)

        if phase == StreamPhase.START:
            self._buffers[chat_id] = ""

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start':
                name = metadata.get('name', '')
                if discord_ch is not None:
                    try:
                        await discord_ch.send(f"⚙️ `{name}`…")
                    except Exception:
                        logger.exception("DiscordChannel: send failed (tool_start)")
            elif kind == 'tool_end' and metadata.get('is_error'):
                result = str(metadata.get('result', ''))
                if discord_ch is not None:
                    try:
                        await discord_ch.send(f"⚠️ `{result}`")
                    except Exception:
                        logger.exception("DiscordChannel: send failed (tool_end)")
            else:
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            if buffered.strip() and discord_ch is not None:
                for i in range(0, len(buffered), _DISCORD_MSG_LIMIT):
                    try:
                        await discord_ch.send(buffered[i:i + _DISCORD_MSG_LIMIT])
                    except Exception:
                        logger.exception("DiscordChannel: send failed (end)")

        elif phase == StreamPhase.ERROR:
            text = text_from_parts(msg.parts) or "Unknown error"
            if discord_ch is not None:
                try:
                    await discord_ch.send(f"❌ {text}")
                except Exception:
                    logger.exception("DiscordChannel: send failed (error)")

        elif phase is None:
            if discord_ch is not None:
                for p in msg.parts:
                    if isinstance(p, AudioPart):
                        try:
                            with open(p.audio, 'rb') as f:
                                await discord_ch.send(file=discord.File(f, filename=Path(p.audio).name))
                        except Exception:
                            logger.exception("DiscordChannel: send audio failed for %r", p.audio)
                text = text_from_parts(msg.parts)
                if text:
                    for i in range(0, len(text), _DISCORD_MSG_LIMIT):
                        try:
                            await discord_ch.send(text[i:i + _DISCORD_MSG_LIMIT])
                        except Exception:
                            logger.exception("DiscordChannel: send failed (direct)")


DiscordBot = DiscordChannel
