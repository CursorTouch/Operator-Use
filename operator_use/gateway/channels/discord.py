"""Discord channel using discord.py."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import ssl
import certifi
import aiohttp
import discord
from discord.ext import commands
import aiohttp.web

from operator_use.bus.views import (
    AudioPart,
    ContentPart,
    FilePart,
    ImagePart,
    IncomingMessage,
    media_paths_from_parts,
    OutgoingMessage,
    StreamPhase,
    TextPart,
    text_from_parts,
)
from operator_use.gateway.channels.config import DiscordConfig
from operator_use.gateway.channels.base import BaseChannel


logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 2000  # Discord message character limit


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


class DiscordChannel(BaseChannel):
    """Discord channel using discord.py."""

    def __init__(self, config: "DiscordConfig", bus=None) -> None:
        super().__init__(config, bus)
        self._bot: Optional[commands.Bot] = None
        self._typing_tasks: dict[int, asyncio.Task] = {}
        self._stream_state: dict[int, dict] = {}  # channel_id -> {message_id, last_edit_time}
        self._last_sent_message_id: dict[int, int] = {}  # channel_id -> message_id
        self._webhook_runner: Optional[aiohttp.web.AppRunner] = None
        self._webhook_site: Optional[aiohttp.web.TCPSite] = None
        self._webhook_stop = asyncio.Event()
        media_dir = self._cfg("media_dir")
        self._media_dir = Path(media_dir) if media_dir else Path.home() / ".operator" / "media"

    # _cfg inherited from BaseChannel

    def _init_bot(self) -> None:
        """Initialize the bot and handlers."""
        if self._bot is not None:
            return

        # Set up intents for message handling
        intents = discord.Intents.all()
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._bot = commands.Bot(command_prefix="!", intents=intents, connector=connector)

        @self._bot.event
        async def on_ready():
            logger.info(f"Discord bot {self._bot.user} connected")

        @self._bot.event
        async def on_message(message: discord.Message):
            if message.author == self._bot.user:
                return
            await self._on_message(message)

        @self._bot.event
        async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
            if self._bot.user and payload.user_id == self._bot.user.id:
                return
            await self._on_reaction(payload, removed=False)

        @self._bot.event
        async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
            if self._bot.user and payload.user_id == self._bot.user.id:
                return
            await self._on_reaction(payload, removed=True)

        @self._bot.event
        async def on_error(event, *args, **kwargs):
            logger.error(f"Discord error in {event}: {args}", exc_info=True)

    @property
    def name(self) -> str:
        return "discord"

    @property
    def use_webhook(self) -> bool:
        return bool(self._cfg("use_webhook"))

    async def start(self) -> None:
        """Start the Discord bot."""
        token = self._cfg("token") or ""
        if not token:
            logger.warning("Discord channel: no token configured, skipping")
            return

        self._init_bot()

        try:
            if self.use_webhook:
                await self._listen_webhook()
            else:
                await self._listen()
        except Exception as e:
            logger.error(f"Failed to start Discord bot: {e}")

    async def stop(self) -> None:
        """Stop the Discord bot."""
        self._webhook_stop.set()

        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        self._typing_tasks.clear()
        self._stream_state.clear()
        self._last_sent_message_id.clear()

        if self._webhook_site:
            await self._webhook_site.stop()
            self._webhook_site = None
        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None

        if self._bot:
            try:
                await self._bot.close()
            except Exception as e:
                logger.debug(f"Discord bot shutdown: {e}")

    async def _listen(self) -> None:
        """Run the Discord bot event loop (WebSocket mode)."""
        token = self._cfg("token") or ""
        if not token:
            return

        try:
            logger.info("Discord bot starting (WebSocket mode)...")
            await self._bot.start(token)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Discord bot error: {e}")

    async def _listen_webhook(self) -> None:
        """Run Discord bot with webhook mode for receiving interactions."""
        token = self._cfg("token") or ""
        webhook_url = self._cfg("webhook_url") or ""
        webhook_path = self._cfg("webhook_path") or "/discord"
        webhook_port = int(self._cfg("webhook_port") or 8080)

        if not webhook_url:
            logger.warning("Discord channel: use_webhook=True but webhook_url not set, skipping")
            return

        # Verify Discord public key for security
        try:
            # Start bot connection in background
            asyncio.create_task(self._bot.start(token))
            await self._bot.wait_until_ready()
            # Get public key from bot application info
            await self._bot.application_info()
            # Note: discord.py doesn't directly expose public key, would need to fetch via API
            logger.info("Discord bot connected (webhook mode)")
        except Exception as e:
            logger.error(f"Failed to initialize Discord bot for webhook: {e}")
            return

        async def handle_discord_interaction(request: aiohttp.web.Request) -> aiohttp.web.Response:
            """Handle Discord interactions via HTTP."""
            try:
                data = await request.json()

                # Handle Discord interaction verification
                if data.get("type") == 1:  # PING interaction
                    return aiohttp.web.json_response({"type": 1})

                # Handle MESSAGE_CREATE events
                if data.get("type") == 2:  # APPLICATION_COMMAND
                    return aiohttp.web.json_response(
                        {"type": 4, "data": {"content": "Command received"}}
                    )

            except Exception as e:
                logger.error(f"Discord webhook error: {e}")
                return aiohttp.web.Response(status=500)

            return aiohttp.web.Response(status=200)

        # Set up webhook server
        app = aiohttp.web.Application()
        app.router.add_post(webhook_path, handle_discord_interaction)
        self._webhook_runner = aiohttp.web.AppRunner(app)
        await self._webhook_runner.setup()
        self._webhook_site = aiohttp.web.TCPSite(self._webhook_runner, "0.0.0.0", webhook_port)
        await self._webhook_site.start()
        logger.info(f"Discord webhook server listening on port {webhook_port}")

        try:
            await asyncio.wait_for(self._webhook_stop.wait(), timeout=None)
        except asyncio.CancelledError:
            pass

    async def _on_reaction(self, payload: discord.RawReactionActionEvent, removed: bool) -> None:
        """Handle reaction add/remove — forward to agent as a reaction event."""
        emoji_str = payload.emoji.name or str(payload.emoji)
        channel_id = str(payload.channel_id)
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=channel_id,
            parts=[TextPart(content=f"[reaction:{emoji_str}]")],
            user_id=str(payload.user_id),
            account_id=self._cfg("account_id") or "",
            metadata={
                "_reaction_event": True,
                "_reaction_emojis": [] if removed else [emoji_str],
                "_reaction_removed_emojis": [emoji_str] if removed else [],
                "_reaction_bot_message_id": payload.message_id,
                "user_id": payload.user_id,
            },
        )
        await self.receive(incoming)

    async def _on_message(self, message: discord.Message) -> None:
        """Handle incoming Discord message."""
        channel_id = message.channel.id
        author_id = message.author.id

        if not self._is_user_allowed(str(author_id)):
            return

        content_parts = []
        media_paths: list[str] = []

        # Extract text
        if message.content:
            content_parts.append(message.content)

        # Extract attachments
        for attachment in message.attachments:
            try:
                self._media_dir.mkdir(parents=True, exist_ok=True)
                file_path = self._media_dir / attachment.filename
                await attachment.save(str(file_path))
                media_paths.append(str(file_path))
                media_type = self._get_media_type(str(file_path))
                content_parts.append(f"[{media_type}: {file_path}]")
            except Exception as e:
                logger.error(f"Failed to download attachment: {e}")
                content_parts.append("[attachment: download failed]")

        content = "\n".join(content_parts) if content_parts else "[empty message]"
        str_channel_id = str(channel_id)

        metadata = {
            "message_id": message.id,
            "user_id": author_id,
            "username": message.author.name,
            "is_group": isinstance(message.channel, discord.TextChannel),
            "channel_type": type(message.channel).__name__,
        }

        # Session control commands — no typing indicator, no agent call
        if not media_paths:
            _parts = content.strip().split(maxsplit=1)
            _cmd_word = _parts[0].lower() if _parts else ""
            if _cmd_word in ("/start", "/stop", "/restart"):
                command = _cmd_word[1:]
                command_args = _parts[1] if len(_parts) > 1 else ""
                incoming = IncomingMessage(
                    channel=self.name,
                    chat_id=str_channel_id,
                    parts=[TextPart(content=content.strip())],
                    user_id=str(author_id),
                    account_id=self._cfg("account_id") or "",
                    metadata={**metadata, "_command": command, "_command_args": command_args},
                )
                await self.receive(incoming)
                return

        self._start_typing(channel_id)

        if media_paths:
            content, parts = await self._process_media_to_parts(
                content, media_paths, [self._get_media_type(p) for p in media_paths]
            )
        else:
            parts = [TextPart(content=content)] if content else []

        incoming = IncomingMessage(
            channel=self.name,
            chat_id=str_channel_id,
            parts=parts,
            user_id=str(author_id),
            account_id=self._cfg("account_id") or "",
            metadata=metadata,
        )
        await self.receive(incoming)

    async def _process_media_to_parts(
        self,
        content: str,
        media_paths: list[str],
        media_types: list[str] | str,
    ) -> tuple[str, list[ContentPart]]:
        """Process media: load images, transcribe audio. Returns (content, parts)."""
        types = (
            media_types
            if isinstance(media_types, list)
            else [media_types] * max(len(media_paths), 1)
        )
        parts: list[TextPart | ImagePart | AudioPart | FilePart] = []
        result_lines: list[str] = []

        for line in content.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue
            found = False
            for i, path in enumerate(media_paths):
                mtype = types[i] if i < len(types) else self._get_media_type(path)
                placeholder = f"[{mtype}: {path}]"
                if placeholder == line_stripped or placeholder in line_stripped:
                    found = True
                    remainder = line_stripped.replace(placeholder, "").strip()
                    if remainder:
                        result_lines.append(remainder)
                        parts.append(TextPart(content=remainder))
                    if mtype in ("voice", "audio"):
                        parts.append(AudioPart(audio=path))
                    elif mtype == "image":
                        # Load image as base64
                        try:
                            from PIL import Image as PILImage
                            import base64
                            from io import BytesIO

                            img = PILImage.open(path).convert("RGB")
                            buf = BytesIO()
                            img.save(buf, format="JPEG", quality=85)
                            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                            parts.append(ImagePart(images=[b64], mime_type="image/jpeg"))
                        except Exception as e:
                            logger.warning(f"Failed to load image {path}: {e}")
                    else:
                        parts.append(FilePart(path=path))
                    break
            if not found and line_stripped:
                result_lines.append(line_stripped)
                parts.append(TextPart(content=line_stripped))

        content_str = (
            "\n".join(result_lines)
            if result_lines
            else ("(image)" if any(isinstance(p, ImagePart) for p in parts) else "[empty message]")
        )
        return content_str, parts

    @staticmethod
    def _get_media_type(path: str) -> str:
        """Guess media type from file extension."""
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext in ("jpg", "jpeg", "png", "gif", "webp"):
            return "image"
        if ext in ("ogg", "wav"):
            return "voice"
        if ext in ("mp3", "m4a", "aac"):
            return "audio"
        return "file"

    async def send(self, message: OutgoingMessage) -> int | None:
        """Send an outgoing message."""
        try:
            channel_id = int(message.chat_id)
        except ValueError:
            logger.error(f"Invalid channel_id: {message.chat_id}")
            return None

        channel = self._bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"Discord channel {channel_id} not found")
            return None

        sent_message_id: int | None = None

        # Handle emoji reactions
        if message.metadata.get("_reaction"):
            emoji = message.metadata.get("_reaction_emoji", "")
            react_msg_id = message.metadata.get("_reaction_message_id")
            if emoji and react_msg_id:
                try:
                    react_msg = await channel.fetch_message(int(react_msg_id))
                    await react_msg.add_reaction(emoji)
                    logger.debug(f"Added reaction {emoji} to message {react_msg_id}")
                except Exception as e:
                    logger.warning(f"Failed to add reaction {emoji} to message {react_msg_id}: {e}")
            return

        # Handle streaming
        phase = message.stream_phase

        if phase == StreamPhase.DONE:
            self._stop_typing(channel_id)
            self._stream_state.pop(channel_id, None)
            pass  # Fall through to send media
        elif phase in (StreamPhase.START, StreamPhase.CHUNK, StreamPhase.END):
            content = text_from_parts(message.parts) or "…"
            stream_state = self._stream_state.get(channel_id)

            if phase == StreamPhase.START and (
                not stream_state or stream_state.get("message_id") is None
            ):
                try:
                    # Get reference message if replying
                    reference = None
                    if message.reply:
                        reply_to_message = self._cfg("reply_to_message", True)
                        reply_msg_id = (
                            message.metadata.get("message_id") if reply_to_message else None
                        )
                        if reply_msg_id:
                            try:
                                reference = await channel.fetch_message(int(reply_msg_id))
                            except Exception:
                                pass

                    first_msg_id = None
                    for chunk in _split_message(content):
                        sent = await channel.send(chunk, reference=reference)
                        if first_msg_id is None:
                            first_msg_id = sent.id  # track first chunk for editing
                        self._last_sent_message_id[channel_id] = sent.id
                    self._stream_state[channel_id] = {
                        "message_id": first_msg_id,
                        "last_edit_time": asyncio.get_running_loop().time(),
                    }
                except Exception as e:
                    logger.warning(f"Discord stream start failed: {e}")
                return

            if not stream_state and (phase == StreamPhase.CHUNK or phase == StreamPhase.END):
                # START was missed — send as a new message and initialize state
                try:
                    sent = await channel.send(content[:MAX_MESSAGE_LEN])
                    self._stream_state[channel_id] = {
                        "message_id": sent.id,
                        "last_edit_time": asyncio.get_running_loop().time(),
                    }
                    self._last_sent_message_id[channel_id] = sent.id
                    stream_state = self._stream_state[channel_id]
                except Exception as e:
                    logger.warning(f"Discord stream recovery failed: {e}")
                    return

            if stream_state and (phase == StreamPhase.CHUNK or phase == StreamPhase.END):
                msg_id = stream_state.get("message_id")
                now = asyncio.get_running_loop().time()
                long_final = phase == StreamPhase.END and len(content) > MAX_MESSAGE_LEN

                if not long_final and (
                    phase == StreamPhase.END or (now - stream_state.get("last_edit_time", 0) >= 1.0)
                ):
                    try:
                        display = (
                            content[:MAX_MESSAGE_LEN] if len(content) > MAX_MESSAGE_LEN else content
                        )
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(content=display)
                        stream_state["last_edit_time"] = now
                    except Exception as e:
                        logger.debug(f"Discord stream edit failed: {e}")

            if phase == StreamPhase.END:
                self._stop_typing(channel_id)
                state = self._stream_state.pop(channel_id, None)
                if state:
                    sent_message_id = state.get("message_id")
                    self._last_sent_message_id[channel_id] = sent_message_id
                if state and len(content) > MAX_MESSAGE_LEN:
                    chunks = _split_message(content, MAX_MESSAGE_LEN)
                    try:
                        # Update first message
                        msg = await channel.fetch_message(state["message_id"])
                        await msg.edit(content=chunks[0])
                        # Send rest as new messages
                        for chunk in chunks[1:]:
                            sent = await channel.send(chunk)
                            self._last_sent_message_id[channel_id] = sent.id
                    except Exception as e:
                        logger.warning(f"Discord stream final send failed: {e}")

            return  # Done with streaming

        if phase == StreamPhase.DONE:
            sent_message_id = self._last_sent_message_id.pop(channel_id, None)

        # Non-streaming or streaming complete: send media, optionally text
        if message.continue_typing:
            self._start_typing(channel_id)
        else:
            self._stop_typing(channel_id)

        # Get reference message if replying
        reference = None
        if message.reply:
            reply_to_message = self._cfg("reply_to_message", True)
            reply_msg_id = message.metadata.get("message_id") if reply_to_message else None
            if reply_msg_id:
                try:
                    reference = await channel.fetch_message(int(reply_msg_id))
                except Exception:
                    pass

        media_paths = media_paths_from_parts(message.parts or [])
        for media_path in media_paths:
            try:
                with open(media_path, "rb") as f:
                    sent = await channel.send(
                        file=discord.File(f, filename=Path(media_path).name), reference=reference
                    )
                    sent_message_id = sent.id
                    self._last_sent_message_id[channel_id] = sent.id
            except Exception as e:
                filename = Path(media_path).name
                logger.error(f"Failed to send media {media_path}: {e}")
                try:
                    await channel.send(f"[Failed to send: {filename}]", reference=reference)
                except Exception as e2:
                    logger.error(f"Failed to send error message: {e2}")

        content = text_from_parts(message.parts) or ""
        # Skip text when DONE (already streamed); only send media
        if content and content != "[empty message]" and phase != StreamPhase.DONE:
            for chunk in _split_message(content):
                try:
                    sent = await channel.send(chunk, reference=reference)
                    sent_message_id = sent.id
                    self._last_sent_message_id[channel_id] = sent.id
                except Exception as e:
                    logger.error(f"Discord send error: {e}")

        return sent_message_id

    def _start_typing(self, channel_id: int) -> None:
        """Start sending typing indicator."""
        self._stop_typing(channel_id)
        self._typing_tasks[channel_id] = asyncio.create_task(self._typing_loop(channel_id))

    def _stop_typing(self, channel_id: int) -> None:
        """Stop the typing indicator."""
        task = self._typing_tasks.pop(channel_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, channel_id: int) -> None:
        """Repeatedly send typing indicator until cancelled."""
        try:
            channel = self._bot.get_channel(channel_id)
            if not channel:
                return
            while self._bot and not self._bot.is_closed():
                async with channel.typing():
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Typing indicator stopped for {channel_id}: {e}")
