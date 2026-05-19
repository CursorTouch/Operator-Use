from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
from program.gateway.channels.discord.utils import _DISCORD_MSG_LIMIT, _MEDIA_DIR, is_audio_attachment

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import discord
    _DISCORD_AVAILABLE = True
else:
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
        self._discord_messages: dict[str, discord.Message] = {}
        self._typing_tasks: dict[str, asyncio.Task] = {}

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
            self._discord_messages[chat_id] = message
            user_id = str(message.author.id)

            parts: list = []

            for attachment in message.attachments:
                if is_audio_attachment(attachment):
                    try:
                        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                        path = _MEDIA_DIR / attachment.filename
                        await attachment.save(path)
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
                message_id=str(message.id),
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

    def _start_typing(self, chat_id: str) -> None:
        """Send typing indicator every 8s until _stop_typing is called."""
        self._stop_typing(chat_id)

        async def _loop() -> None:
            while True:
                ch = self._discord_channels.get(chat_id)
                if ch is not None:
                    try:
                        await ch.trigger_typing()  # type: ignore[reportAttributeAccessIssue]
                    except Exception:
                        pass
                await asyncio.sleep(8)

        self._typing_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to the Discord channel."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata
        discord_ch = self._discord_channels.get(chat_id)

        if phase == StreamPhase.START:
            self._buffers[chat_id] = ""
            self._start_typing(chat_id)

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
            self._stop_typing(chat_id)
            buffered = self._buffers.pop(chat_id, "")
            if buffered.strip() and discord_ch is not None:
                for i in range(0, len(buffered), _DISCORD_MSG_LIMIT):
                    try:
                        await discord_ch.send(buffered[i:i + _DISCORD_MSG_LIMIT])
                    except Exception:
                        logger.exception("DiscordChannel: send failed (end)")

        elif phase == StreamPhase.ERROR:
            self._stop_typing(chat_id)
            text = text_from_parts(msg.parts) or "Unknown error"
            if discord_ch is not None:
                try:
                    await discord_ch.send(f"❌ {text}")
                except Exception:
                    logger.exception("DiscordChannel: send failed (error)")

        elif phase is None:
            kind = metadata.get('kind')
            if kind == 'react':
                emoji = metadata.get('emoji', '👍')
                message_id = metadata.get('message_id')
                discord_msg = self._discord_messages.get(chat_id)
                if discord_msg is not None and str(discord_msg.id) == str(message_id):
                    try:
                        await discord_msg.add_reaction(emoji)
                    except Exception:
                        logger.exception("DiscordChannel: add_reaction failed for %r", chat_id)
                elif discord_ch is not None and message_id is not None:
                    try:
                        fetched = await discord_ch.fetch_message(int(message_id))  # type: ignore[union-attr]
                        await fetched.add_reaction(emoji)
                    except Exception:
                        logger.exception("DiscordChannel: fetch_message/add_reaction failed for %r", message_id)
                return

            if discord_ch is not None:
                reply_to = metadata.get('reply_to')
                reference = None
                if reply_to:
                    try:
                        reference = discord.MessageReference(
                            message_id=int(reply_to),
                            channel_id=int(chat_id),
                            fail_if_not_exists=False,
                        )
                    except Exception:
                        pass

                for p in msg.parts:
                    match p:
                        case AudioPart(audio=audio):
                            try:
                                with open(audio, 'rb') as f:
                                    dfile = discord.File(f, filename=Path(audio).name)  # type: ignore[possibly-unbound]
                                    if reference is not None:
                                        await discord_ch.send(file=dfile, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                                    else:
                                        await discord_ch.send(file=dfile)
                            except Exception:
                                logger.exception("DiscordChannel: send audio failed for %r", audio)
                        case FilePart(path=fp):
                            try:
                                caption = text_from_parts(msg.parts) or None
                                with open(fp, 'rb') as f:
                                    dfile = discord.File(f, filename=Path(fp).name)  # type: ignore[possibly-unbound]
                                    if reference is not None:
                                        await discord_ch.send(content=caption, file=dfile, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                                    else:
                                        await discord_ch.send(content=caption, file=dfile)
                            except Exception:
                                logger.exception("DiscordChannel: send_file failed for %r", fp)
                            return  # caption already sent with the file
                text = text_from_parts(msg.parts)
                if text:
                    for i in range(0, len(text), _DISCORD_MSG_LIMIT):
                        chunk = text[i:i + _DISCORD_MSG_LIMIT]
                        try:
                            if i == 0 and reference is not None:
                                await discord_ch.send(chunk, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                            else:
                                await discord_ch.send(chunk)
                        except Exception:
                            logger.exception("DiscordChannel: send failed (direct)")


DiscordBot = DiscordChannel
