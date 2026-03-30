"""Telegram channel using python-telegram-bot (polling or webhook)."""

import asyncio
import logging
import re
from pathlib import Path

import aiohttp.web
from telegram import BotCommand, InputFile, ReplyParameters, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)
import json
from telegram.request import HTTPXRequest
from telegram.error import NetworkError

import base64
from io import BytesIO

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
from operator_use.gateway.channels.config import TelegramConfig
from operator_use.gateway.channels.base import BaseChannel


logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000  # Telegram message character limit

# Supported emoji for Telegram setMessageReaction (Bot API 7.0)
TELEGRAM_ALLOWED_REACTIONS = {
    "👍",
    "👎",
    "❤",
    "🔥",
    "🥰",
    "👏",
    "😁",
    "🤔",
    "🤯",
    "😱",
    "🤬",
    "😢",
    "🎉",
    "🤩",
    "🤮",
    "💩",
    "🙏",
    "👌",
    "🕊",
    "🤡",
    "🥱",
    "🥴",
    "😍",
    "🐳",
    "❤‍🔥",
    "🌚",
    "🌭",
    "💯",
    "🤣",
    "⚡",
    "🍌",
    "🏆",
    "💔",
    "🤨",
    "😐",
    "🍓",
    "🍾",
    "💋",
    "🖕",
    "😈",
    "😴",
    "😭",
    "🤓",
    "👻",
    "👨‍💻",
    "👀",
    "🎃",
    "🙈",
    "😇",
    "😨",
    "🤝",
    "✍",
    "🤗",
    "🫡",
    "🎅",
    "🎄",
    "☃",
    "💅",
    "🤪",
    "🗿",
    "🆒",
    "💘",
    "🙉",
    "🦄",
    "😘",
    "💊",
    "🙊",
    "😎",
    "👾",
    "🤷‍♂",
    "🤷",
    "🤷‍♀",
    "😡",
}


def _markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML."""
    if not text:
        return ""

    # 1. Extract and protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Headers -> plain text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Blockquotes -> plain text
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Escape HTML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def _get_media_extension(media_type: str, mime_type: str | None) -> str:
    """Get file extension for media type."""
    if mime_type:
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
        }
        if mime_type in ext_map:
            return ext_map[mime_type]
    type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}
    return type_map.get(media_type, "")


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


class TelegramChannel(BaseChannel):
    """Telegram channel using long-polling or webhook."""

    BOT_COMMANDS = [
        BotCommand("start", "Start a new session"),
        BotCommand("stop", "Stop and save the current session"),
        BotCommand("restart", "Reboot the entire system"),
        BotCommand("help", "Show available commands"),
    ]

    def __init__(self, config: TelegramConfig, bus=None) -> None:
        super().__init__(config, bus)
        self._app = None
        self._webhook_runner: aiohttp.web.AppRunner | None = None
        self._webhook_site: aiohttp.web.TCPSite | None = None
        self._webhook_stop = asyncio.Event()
        self._polling_running = False
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._media_group_buffers: dict[str, dict] = {}
        self._media_group_tasks: dict[str, asyncio.Task] = {}
        self._stream_state: dict[str, dict] = {}  # chat_id -> {message_id, last_edit_time}
        self._last_sent_message_id: dict[
            str, int
        ] = {}  # chat_id -> message_id (for streamed replies)
        media_dir = self._cfg("media_dir")
        self._media_dir = Path(media_dir) if media_dir else Path.home() / ".operator" / "media"

    # _cfg inherited from BaseChannel

    def _image_to_base64(self, path: str) -> tuple[str, str]:
        """Load image and return (base64_string, mime_type)."""
        try:
            from PIL import Image as PILImage

            img = PILImage.open(path).convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("utf-8"), "image/jpeg"
        except Exception as e:
            logger.warning("Failed to load image %s: %s", path, e)
            return "", "image/jpeg"

    async def _process_media_to_parts(
        self,
        content: str,
        media_paths: list[str],
        media_types: list[str] | str,
    ) -> tuple[str, list[ContentPart]]:
        """Process media in channel: transcribe voice/audio, load images. Returns (content, parts)."""
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
            # Check if line is a placeholder [type: path]
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
                        parts.append(AudioPart(audio=path))  # Agent does STT
                    elif mtype == "image":
                        b64, mime = self._image_to_base64(path)
                        if b64:
                            parts.append(ImagePart(images=[b64], mime_type=mime))
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

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def use_webhook(self) -> bool:
        return bool(self._cfg("use_webhook"))

    async def start(self) -> None:
        token = self._cfg("token") or ""
        if not token:
            logger.warning("Telegram channel: no token configured, skipping")
            return

        req = HTTPXRequest(
            connection_pool_size=16,
            pool_timeout=5.0,
            connect_timeout=30.0,
            read_timeout=30.0,
        )
        builder = ApplicationBuilder().token(token).request(req)
        if self.use_webhook:
            builder = builder.updater(None)
        else:
            builder = builder.get_updates_request(req)

        proxy = self._cfg("proxy")
        if proxy:
            builder = builder.proxy(proxy).get_updates_proxy(proxy)

        async def _post_init(app):
            try:
                await app.bot.set_my_commands(self.BOT_COMMANDS)
            except Exception as e:
                logger.debug("Could not register bot commands: %s", e)

        builder = builder.post_init(_post_init)
        self._app = builder.build()

        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("stop", self._on_stop))
        self._app.add_handler(CommandHandler("restart", self._on_restart))
        self._app.add_handler(CommandHandler("new", self._on_stop))  # /new kept as stop alias
        self._app.add_handler(
            MessageHandler(
                (
                    filters.TEXT
                    | filters.PHOTO
                    | filters.VOICE
                    | filters.AUDIO
                    | filters.Document.ALL
                )
                & ~filters.COMMAND,
                self._on_message,
            )
        )
        self._app.add_handler(MessageReactionHandler(self._on_reaction))
        self._app.add_error_handler(self._on_error)

        await self._listen()

    async def stop(self) -> None:
        self._webhook_stop.set()
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)
        for task in self._media_group_tasks.values():
            task.cancel()
        self._media_group_tasks.clear()
        self._media_group_buffers.clear()
        self._stream_state.clear()
        if self._webhook_site:
            await self._webhook_site.stop()
            self._webhook_site = None
        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None
        if self._app:
            self._polling_running = False
            # Do NOT call stop_running() - it calls loop.stop() and aborts shutdown.
            # Our channel.start() is already cancelled; updater.stop() + app.stop() suffice.
            try:
                if self._app.updater:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Telegram shutdown: %s", e)
            self._app = None

    async def _listen(self) -> None:
        if self.use_webhook:
            await self._listen_webhook()
        else:
            await self._listen_polling()

    async def _listen_polling(self) -> None:
        """Manual polling lifecycle for clean shutdown (avoids unawaited coroutine warnings)."""
        if not self._app or not self._app.updater:
            return
        self._polling_running = True
        await self._app.initialize()
        if self._app.post_init:
            await self._app.post_init(self._app)
        await self._app.start()
        try:
            await self._app.updater.start_polling(
                allowed_updates=["message", "message_reaction"],
                drop_pending_updates=True,
            )
        except NetworkError:
            # Suppress transient network errors - bot will retry automatically
            pass
        bot_info = await self._app.bot.get_me()
        logger.info("Telegram bot @%s connected", bot_info.username)
        while self._polling_running:
            await asyncio.sleep(1)

    async def _listen_webhook(self) -> None:
        webhook_url = self._cfg("webhook_url") or ""
        webhook_path = self._cfg("webhook_path") or "/telegram"
        webhook_port = int(self._cfg("webhook_port") or 8080)

        if not webhook_url:
            logger.warning("Telegram channel: use_webhook=True but webhook_url not set, skipping")
            return

        full_url = f"{webhook_url.rstrip('/')}{webhook_path}"
        await self._app.bot.set_webhook(url=full_url, allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram webhook set to %s", full_url)

        async def handle_telegram(request: aiohttp.web.Request) -> aiohttp.web.Response:
            try:
                data = await request.json()
                update = Update.de_json(data, self._app.bot)
                await self._app.update_queue.put(update)
            except Exception as e:
                logger.error(f"Telegram webhook error: {e}")
                return aiohttp.web.Response(status=500)
            return aiohttp.web.Response()

        app = aiohttp.web.Application()
        app.router.add_post(webhook_path, handle_telegram)
        self._webhook_runner = aiohttp.web.AppRunner(app)
        await self._webhook_runner.setup()
        self._webhook_site = aiohttp.web.TCPSite(self._webhook_runner, "0.0.0.0", webhook_port)
        await self._webhook_site.start()
        logger.info(f"Telegram webhook server listening on port {webhook_port}")

        async with self._app:
            await self._app.start()
            try:
                await asyncio.wait_for(self._webhook_stop.wait(), timeout=None)
            except asyncio.CancelledError:
                pass

    @staticmethod
    def _sender_id(user) -> str:
        """Build sender_id with username for allowlist matching."""
        sid = str(user.id)
        return f"{sid}|{user.username}" if user.username else sid

    @staticmethod
    def _build_chat_id(chat_id: str, thread_id: int | None) -> str:
        """Build chat_id, appending thread_id for topic messages."""
        if thread_id:
            return f"{chat_id}:{thread_id}"
        return chat_id

    @staticmethod
    def _parse_chat_id(chat_id: str) -> tuple[int, int | None]:
        """Split chat_id into (telegram_chat_id, thread_id)."""
        if ":" in chat_id:
            raw, thread = chat_id.rsplit(":", 1)
            return int(raw), int(thread)
        return int(chat_id), None

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return

        msg = update.message
        user = update.effective_user
        raw_chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        thread_id = msg.message_thread_id
        chat_id = raw_chat_id
        sender_id = self._sender_id(user)

        if not self._is_user_allowed(sender_id):
            return

        content_parts = []
        media_paths: list[str] = []

        if msg.text:
            content_parts.append(msg.text)
        if msg.caption:
            content_parts.append(msg.caption)

        media_file = None
        media_type = None
        if msg.photo:
            media_file = msg.photo[-1]
            media_type = "image"
        elif msg.voice:
            media_file = msg.voice
            media_type = "voice"
        elif msg.audio:
            media_file = msg.audio
            media_type = "audio"
        elif msg.document:
            media_file = msg.document
            media_type = "file"

        if media_file and self._app:
            try:
                self._media_dir.mkdir(parents=True, exist_ok=True)
                file = await self._app.bot.get_file(media_file.file_id)
                ext = _get_media_extension(media_type, getattr(media_file, "mime_type", None))
                file_path = self._media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))
                media_paths.append(str(file_path))
                content_parts.append(f"[{media_type}: {file_path}]")
            except Exception as e:
                logger.error("Failed to download media: %s", e)
                content_parts.append(f"[{media_type}: download failed]")

        content = "\n".join(content_parts) if content_parts else "[empty message]"
        str_chat_id = chat_id

        # Media groups: buffer briefly, forward as one aggregated turn
        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            key = f"{str_chat_id}:{media_group_id}"
            if key not in self._media_group_buffers:
                self._media_group_buffers[key] = {
                    "sender_id": sender_id,
                    "chat_id": str_chat_id,
                    "contents": [],
                    "media": [],
                    "media_types": [],
                    "metadata": {
                        "message_id": msg.message_id,
                        "user_id": user.id,
                        "username": user.username,
                        "first_name": user.first_name,
                        "is_group": msg.chat.type != "private",
                        "thread_id": thread_id,
                    },
                }
                self._start_typing(str_chat_id)
            buf = self._media_group_buffers[key]
            if content and content != "[empty message]":
                buf["contents"].append(content)
            buf["media"].extend(media_paths)
            buf["media_types"].extend([media_type] * len(media_paths))
            if key not in self._media_group_tasks:
                self._media_group_tasks[key] = asyncio.create_task(self._flush_media_group(key))
            return

        self._start_typing(str_chat_id)

        metadata = {
            "message_id": msg.message_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_group": msg.chat.type != "private",
            "chat_type": msg.chat.type,
            "thread_id": thread_id,
        }

        if media_paths:
            content, parts = await self._process_media_to_parts(
                content, media_paths, [media_type] * len(media_paths)
            )
        else:
            parts = [TextPart(content=content)] if content else []

        incoming = IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            parts=parts,
            user_id=str(user.id),
            account_id=self._cfg("account_id") or "",
            metadata=metadata,
        )
        await self.receive(incoming)

    async def _flush_media_group(self, key: str) -> None:
        """Wait briefly, then forward buffered media-group as one turn."""
        try:
            await asyncio.sleep(0.6)
            buf = self._media_group_buffers.pop(key, None)
            if not buf:
                return
            content = "\n".join(buf["contents"]) or "[empty message]"
            media_paths = list(dict.fromkeys(buf["media"]))
            media_types = buf.get("media_types", [])

            if media_paths:
                content, parts = await self._process_media_to_parts(
                    content,
                    media_paths,
                    media_types or [self._get_media_type(p) for p in media_paths],
                )
            else:
                parts = [TextPart(content=content)] if content else []

            incoming = IncomingMessage(
                channel=self.name,
                chat_id=buf["chat_id"],
                parts=parts,
                user_id=buf["sender_id"].split("|")[0],
                account_id=self._cfg("account_id") or "",
                metadata=buf["metadata"],
            )
            await self.receive(incoming)
        finally:
            self._media_group_tasks.pop(key, None)

    @staticmethod
    def _get_media_type(path: str) -> str:
        """Guess media type from file extension. Use voice for .ogg/.wav so it displays as a voice bubble (no filename)."""
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext in ("jpg", "jpeg", "png", "gif", "webp"):
            return "photo"
        if ext in ("ogg", "wav"):
            return "voice"
        if ext in ("mp3", "m4a", "aac"):
            return "audio"
        return "document"

    async def send(self, message: OutgoingMessage) -> int | None:
        """Send an outgoing message. Returns the Telegram message_id of the last sent message."""
        if not self._app or not self._app.bot:
            logger.warning("Telegram channel: app not running, cannot send")
            return None

        chat_id = message.chat_id
        sent_message_id: int | None = None

        try:
            chat_id_int, thread_id = self._parse_chat_id(chat_id)
        except ValueError:
            logger.error("Invalid chat_id: %s", chat_id)
            return None

        # Handle emoji reactions
        if message.metadata.get("_reaction"):
            emoji = message.metadata.get("_reaction_emoji", "")
            react_msg_id = message.metadata.get("_reaction_message_id")
            if emoji and react_msg_id:
                if emoji not in TELEGRAM_ALLOWED_REACTIONS:
                    logger.warning(
                        "Emoji '%s' is not in Telegram's allowed reaction set — attempting anyway",
                        emoji,
                    )
                try:
                    from telegram import ReactionTypeEmoji

                    await self._app.bot.set_message_reaction(
                        chat_id=chat_id_int,
                        message_id=int(react_msg_id),
                        reaction=[ReactionTypeEmoji(emoji=emoji)],
                        is_big=False,
                    )
                    logger.debug("Reacted with %s to message %s", emoji, react_msg_id)
                except Exception as e:
                    logger.warning(
                        "Failed to set reaction %s on message %s: %s", emoji, react_msg_id, e
                    )
            return

        # Streaming: stream_phase on OutgoingMessage
        phase = message.stream_phase

        if phase == StreamPhase.DONE:
            self._stop_typing(chat_id)
            self._stream_state.pop(chat_id, None)
            # Fall through to send media (e.g. TTS audio); text was already streamed
            pass
        elif phase in (StreamPhase.START, StreamPhase.CHUNK, StreamPhase.END):
            content = text_from_parts(message.parts) or "…"
            stream_state = self._stream_state.get(chat_id)

            reply_params = None
            if message.reply:
                reply_to_message = self._cfg("reply_to_message", True)
                reply_to = message.metadata.get("message_id") if reply_to_message else None
                if reply_to:
                    try:
                        reply_params = ReplyParameters(
                            message_id=int(reply_to),
                            allow_sending_without_reply=True,
                        )
                    except (ValueError, TypeError):
                        pass

            if phase == StreamPhase.START and (
                not stream_state or stream_state.get("message_id") is None
            ):
                try:
                    html = _markdown_to_telegram_html(content)
                    sent = await self._app.bot.send_message(
                        chat_id=chat_id_int,
                        text=html,
                        parse_mode="HTML",
                        reply_parameters=reply_params,
                        message_thread_id=thread_id,
                    )
                    sent_message_id = sent.message_id
                    self._last_sent_message_id[chat_id] = sent.message_id
                    self._stream_state[chat_id] = {
                        "message_id": sent.message_id,
                        "last_edit_time": asyncio.get_running_loop().time(),
                    }
                except Exception as e:
                    logger.warning("Telegram stream start failed: %s", e)
                return

            if not stream_state and (phase == StreamPhase.CHUNK or phase == StreamPhase.END):
                # START was missed — send as a new message and initialize state
                try:
                    html = _markdown_to_telegram_html(content[:MAX_MESSAGE_LEN])
                    sent = await self._app.bot.send_message(
                        chat_id=chat_id_int,
                        text=html,
                        parse_mode="HTML",
                        message_thread_id=thread_id,
                    )
                    self._stream_state[chat_id] = {
                        "message_id": sent.message_id,
                        "last_edit_time": asyncio.get_running_loop().time(),
                    }
                    self._last_sent_message_id[chat_id] = sent.message_id
                    stream_state = self._stream_state[chat_id]
                except Exception as e:
                    logger.warning("Telegram stream recovery failed: %s", e)
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
                        html = _markdown_to_telegram_html(display)
                        await self._app.bot.edit_message_text(
                            chat_id=chat_id_int,
                            message_id=msg_id,
                            text=html,
                            parse_mode="HTML",
                        )
                        stream_state["last_edit_time"] = now
                    except Exception as e:
                        logger.debug("Telegram stream edit failed: %s", e)

            if phase == StreamPhase.END:
                self._stop_typing(chat_id)
                state = self._stream_state.pop(chat_id, None)
                if state:
                    sent_message_id = state.get("message_id")
                    self._last_sent_message_id[chat_id] = sent_message_id
                if state and len(content) > MAX_MESSAGE_LEN:
                    chunks = _split_message(content, MAX_MESSAGE_LEN)
                    try:
                        html = _markdown_to_telegram_html(chunks[0])
                        await self._app.bot.edit_message_text(
                            chat_id=chat_id_int,
                            message_id=state["message_id"],
                            text=html,
                            parse_mode="HTML",
                        )
                        for chunk in chunks[1:]:
                            html = _markdown_to_telegram_html(chunk)
                            sent = await self._app.bot.send_message(
                                chat_id=chat_id_int,
                                text=html,
                                parse_mode="HTML",
                                message_thread_id=thread_id,
                            )
                            # Keep the latest message ID for the bot trace
                            self._last_sent_message_id[chat_id] = sent.message_id
                    except Exception as e:
                        logger.warning("Telegram stream final send failed: %s", e)

            return  # Done with streaming phase; DONE falls through to send media

        if phase == StreamPhase.DONE:
            sent_message_id = self._last_sent_message_id.pop(chat_id, None)

        # Non-streaming or streaming complete (DONE): send media, optionally text
        if message.continue_typing:
            self._start_typing(chat_id)
        else:
            self._stop_typing(chat_id)

        reply_params = None
        if message.reply:
            reply_to_message = self._cfg("reply_to_message", True)
            reply_to = message.metadata.get("message_id") if reply_to_message else None
            if reply_to:
                try:
                    reply_params = ReplyParameters(
                        message_id=int(reply_to),
                        allow_sending_without_reply=True,
                    )
                except (ValueError, TypeError):
                    pass

        media_paths = media_paths_from_parts(message.parts or [])
        for media_path in media_paths:
            try:
                media_type = self._get_media_type(media_path)
                sender = {
                    "photo": self._app.bot.send_photo,
                    "voice": self._app.bot.send_voice,
                    "audio": self._app.bot.send_audio,
                }.get(media_type, self._app.bot.send_document)
                param = (
                    "photo"
                    if media_type == "photo"
                    else media_type
                    if media_type in ("voice", "audio")
                    else "document"
                )
                with open(media_path, "rb") as f:
                    if media_type == "voice":
                        file_obj = InputFile(f, filename="voice.ogg")
                    elif media_type == "audio":
                        file_obj = InputFile(f, filename="audio.mp3")
                    else:
                        file_obj = f
                    await sender(
                        chat_id=chat_id_int,
                        **{param: file_obj},
                        reply_parameters=reply_params,
                        message_thread_id=thread_id,
                    )
            except Exception as e:
                filename = Path(media_path).name
                logger.error("Failed to send media %s: %s", media_path, e)
                await self._app.bot.send_message(
                    chat_id=chat_id_int,
                    text=f"[Failed to send: {filename}]",
                    reply_parameters=reply_params,
                    message_thread_id=thread_id,
                )

        content = text_from_parts(message.parts) or ""
        # Skip text when DONE (already streamed); only send media (e.g. TTS)
        if content and content != "[empty message]" and phase != StreamPhase.DONE:
            for chunk in _split_message(content):
                try:
                    html = _markdown_to_telegram_html(chunk)
                    sent = await self._app.bot.send_message(
                        chat_id=chat_id_int,
                        text=html,
                        parse_mode="HTML",
                        reply_parameters=reply_params,
                        message_thread_id=thread_id,
                    )
                    sent_message_id = sent.message_id
                    self._last_sent_message_id[chat_id] = sent.message_id
                except Exception as e:
                    logger.warning("Telegram HTML send failed, falling back to plain text: %s", e)
                    try:
                        sent = await self._app.bot.send_message(
                            chat_id=chat_id_int,
                            text=chunk,
                            reply_parameters=reply_params,
                            message_thread_id=thread_id,
                        )
                        sent_message_id = sent.message_id
                        self._last_sent_message_id[chat_id] = sent_message_id
                    except Exception as e2:
                        logger.error("Telegram send error: %s", e2)

        return sent_message_id

    def _start_typing(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat."""
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send 'typing' action until cancelled."""
        try:
            chat_id_int, thread_id = self._parse_chat_id(chat_id)
            while self._app and self._app.bot:
                await self._app.bot.send_chat_action(
                    chat_id=chat_id_int, action="typing", message_thread_id=thread_id
                )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Typing indicator stopped for %s: %s", chat_id, e)

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start — register group for setup-channels in groups, start session in DMs."""
        if not update.message or not update.effective_chat:
            return
        chat = update.effective_chat
        if chat.type in ("group", "supergroup"):
            try:
                from operator_use.config.paths import get_userdata_dir

                pending = {
                    "channel": "telegram",
                    "chat_id": str(chat.id),
                    "chat_title": chat.title or str(chat.id),
                    "chat_type": chat.type,
                }
                pending_path = get_userdata_dir() / "pending_setup.json"
                pending_path.write_text(json.dumps(pending), encoding="utf-8")
                await update.message.reply_text(
                    "✓ Group registered!\n\nRun `operator setup-channels` in your terminal to create topics for each agent."
                )
                logger.info("Registered group %s (%s) for setup-channels", chat.id, chat.title)
            except Exception as e:
                logger.error("Failed to register group: %s", e)
        else:
            thread_id = update.message.message_thread_id
            chat_id = self._build_chat_id(str(chat.id), thread_id)
            args = " ".join(context.args) if context.args else ""
            incoming = IncomingMessage(
                channel=self.name,
                chat_id=chat_id,
                parts=[TextPart(content="/start" + (f" {args}" if args else ""))],
                user_id=str(update.effective_user.id) if update.effective_user else "",
                account_id=self._cfg("account_id") or "",
                metadata={"_command": "start", "_command_args": args, "thread_id": thread_id},
            )
            await self.receive(incoming)

    async def _on_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stop — archive the current session."""
        if not update.message or not update.effective_chat:
            return
        chat = update.effective_chat
        thread_id = update.message.message_thread_id
        chat_id = self._build_chat_id(str(chat.id), thread_id)
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            parts=[TextPart(content="/stop")],
            user_id=str(update.effective_user.id) if update.effective_user else "",
            account_id=self._cfg("account_id") or "",
            metadata={"_command": "stop", "thread_id": thread_id},
        )
        await self.receive(incoming)

    async def _on_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /restart (and /new alias) — clear session history and start fresh."""
        if not update.message or not update.effective_chat:
            return
        chat = update.effective_chat
        thread_id = update.message.message_thread_id
        chat_id = self._build_chat_id(str(chat.id), thread_id)
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            parts=[TextPart(content="/restart")],
            user_id=str(update.effective_user.id) if update.effective_user else "",
            account_id=self._cfg("account_id") or "",
            metadata={"_command": "restart", "thread_id": thread_id},
        )
        await self.receive(incoming)

    async def _on_reaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle message reaction updates — forward to agent as a reaction event."""
        reaction_update = update.message_reaction
        if not reaction_update:
            return

        new_emojis = [r.emoji for r in (reaction_update.new_reaction or []) if hasattr(r, "emoji")]
        old_emojis = [r.emoji for r in (reaction_update.old_reaction or []) if hasattr(r, "emoji")]

        # Nothing changed that we care about
        if not new_emojis and not old_emojis:
            return

        chat_id = str(reaction_update.chat.id)
        user = reaction_update.user
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=chat_id,
            parts=[TextPart(content=f"[reaction:{','.join(new_emojis or old_emojis)}]")],
            user_id=str(user.id) if user else "",
            metadata={
                "_reaction_event": True,
                "_reaction_emojis": new_emojis,
                "_reaction_removed_emojis": old_emojis,
                "_reaction_bot_message_id": reaction_update.message_id,
                "user_id": user.id if user else None,
                "username": getattr(user, "username", None),
            },
        )
        await self.receive(incoming)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log polling/handler errors."""
        logger.error("Telegram error: %s", context.error)
