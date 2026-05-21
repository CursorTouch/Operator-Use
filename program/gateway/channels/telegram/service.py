from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from program.gateway.channels.shutdown import quiet_library_logging
from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
from program.gateway.channels.telegram.utils import _MEDIA_DIR, audio_mime_ext, markdown_to_telegram_html, split_message

logger = logging.getLogger(__name__)

try:
    from telegram import Bot, BotCommand, InputFile, Update
    from telegram.constants import ChatAction
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
    _PTB_AVAILABLE = True
except ImportError:
    _PTB_AVAILABLE = False


class TelegramChannel(BaseChannel):
    """
    A single Telegram bot that handles all chats.

    Supports text messages, voice notes, and audio files incoming.
    Outgoing supports text and audio (TTS) via direct send.

    Requires: pip install "python-telegram-bot>=20.0"
    """

    def __init__(
        self,
        token: str,
        commands: list[tuple[str, str]] | None = None,
        allow_from: list[str] | None = None,
        group_policy: str = "mention",
        show_tool_calls: bool = True,
        streaming: bool = True,
        streaming_latency: float = 1.0,
    ) -> None:
        super().__init__()
        if not _PTB_AVAILABLE:
            raise ImportError('python-telegram-bot>=20.0 is required for TelegramChannel.')
        self._token = token
        self._commands = commands or []
        self._allow_from = set(allow_from or [])
        self._group_policy = group_policy
        self._show_tool_calls = show_tool_calls
        self._streaming = streaming
        self._streaming_latency = streaming_latency
        self._is_group: dict[str, bool] = {}  # chat_id → True if group/supergroup/channel
        self._buffers: dict[str, str] = {}
        self._app: Application | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._live_tasks: dict[str, asyncio.Task] = {}
        self._live_msg_ids: dict[str, int | None] = {}
        self._retry_msg_ids: dict[str, int] = {}  # chat_id → rolling retry-status message ID
        self._tool_msg_ids: dict[str, int] = {}   # chat_id → rolling tool-status message ID

    @property
    def channel_id(self) -> str:
        return "telegram"

    async def connect(self) -> None:
        """Build PTB app, register handlers, start polling. Runs until cancelled."""
        self._app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message:
                return
            msg = update.message
            chat_id = str(msg.chat_id)
            user_id = str(msg.from_user.id) if msg.from_user else ""

            if self._allow_from and user_id not in self._allow_from:
                return

            self._is_group[chat_id] = msg.chat.type in ("group", "supergroup", "channel")

            # Group policy: in group/supergroup chats, respond only when mentioned
            if self._group_policy == "mention" and msg.chat.type in ("group", "supergroup"):
                bot_user = self._app.bot.username if self._app else None  # type: ignore[union-attr]
                text_for_check = msg.text or msg.caption or ""
                mentioned = bool(bot_user) and f"@{bot_user}" in text_for_check
                is_reply_to_bot = (
                    msg.reply_to_message is not None
                    and msg.reply_to_message.from_user is not None
                    and msg.reply_to_message.from_user.is_bot
                    and msg.reply_to_message.from_user.username == bot_user
                )
                if not mentioned and not is_reply_to_bot:
                    return

            parts: list = []

            if msg.voice:
                try:
                    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                    file = await ctx.bot.get_file(msg.voice.file_id)
                    path = _MEDIA_DIR / f"{msg.voice.file_id[:20]}.ogg"
                    await file.download_to_drive(str(path))
                    parts.append(AudioPart(audio=str(path), mime_type='audio/ogg'))
                    await ctx.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
                except Exception:
                    logger.exception("TelegramChannel: failed to download voice message")
                    return

            elif msg.audio:
                try:
                    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                    file = await ctx.bot.get_file(msg.audio.file_id)
                    mime = msg.audio.mime_type or 'audio/mpeg'
                    ext = audio_mime_ext(mime)
                    path = _MEDIA_DIR / f"{msg.audio.file_id[:20]}{ext}"
                    await file.download_to_drive(str(path))
                    parts.append(AudioPart(audio=str(path), mime_type=mime))
                    await ctx.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
                except Exception:
                    logger.exception("TelegramChannel: failed to download audio message")
                    return

            elif msg.text:
                text = msg.text.strip()
                if not text:
                    return
                parts.append(TextPart(text))
                await ctx.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)

            if not parts:
                return

            meta: dict = {}
            if msg.reply_to_message:
                meta['reply_to'] = str(msg.reply_to_message.message_id)

            await self.receive(IncomingMessage(
                channel="telegram",
                chat_id=chat_id,
                parts=parts,
                user_id=user_id,
                message_id=str(msg.message_id),
                metadata=meta,
            ))

        self._app.add_handler(MessageHandler(
            filters.TEXT | filters.VOICE | filters.AUDIO,
            _on_message,
        ))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started (polling)")

        # Sync the bot's '/' command menu with the real command registry.
        # An empty list wipes any stale commands left on Telegram's servers.
        import re
        valid = [
            BotCommand(name, (desc or name)[:256])
            for name, desc in self._commands
            if re.fullmatch(r'[a-z0-9_]{1,32}', name)
        ]
        try:
            await self._app.bot.set_my_commands(valid)
        except Exception:
            logger.exception("TelegramChannel: failed to sync bot commands")

        try:
            await asyncio.Future()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        """Stop PTB app and release resources."""
        if self._app is not None:
            with quiet_library_logging("telegram", "httpx") as debug:
                try:
                    await self._app.updater.stop()
                    await self._app.stop()
                    await self._app.shutdown()
                except Exception:
                    if debug:
                        logger.exception("TelegramChannel: error during disconnect")
            self._app = None

    def _start_typing(self, chat_id: str) -> None:
        """Send typing action every 4s until _stop_typing is called."""
        self._stop_typing(chat_id)

        async def _loop() -> None:
            while True:
                if self._app is not None:
                    try:
                        await self._app.bot.send_chat_action(int(chat_id), ChatAction.TYPING)
                    except Exception:
                        pass
                await asyncio.sleep(4)

        self._typing_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    def _start_live_streaming(self, chat_id: str) -> None:
        self._stop_live_streaming(chat_id)
        self._live_msg_ids[chat_id] = None
        latency = self._streaming_latency

        async def _loop() -> None:
            await asyncio.sleep(latency)
            while True:
                buffered = self._buffers.get(chat_id, "")
                if buffered.strip() and self._app is not None:
                    bot = self._app.bot
                    existing = self._live_msg_ids.get(chat_id)
                    if existing is None:
                        try:
                            sent = await bot.send_message(
                                int(chat_id),
                                markdown_to_telegram_html(buffered),
                                parse_mode="HTML",
                            )
                            self._live_msg_ids[chat_id] = sent.message_id
                        except Exception:
                            logger.exception("TelegramChannel: live send_message failed")
                        # Posting a real message clears the client-side typing
                        # indicator; re-trigger so it stays visible while streaming.
                        try:
                            await bot.send_chat_action(int(chat_id), ChatAction.TYPING)
                        except Exception:
                            pass
                    else:
                        try:
                            await bot.edit_message_text(
                                markdown_to_telegram_html(buffered),
                                chat_id=int(chat_id),
                                message_id=existing,
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass  # edit conflicts during rapid chunks are benign
                await asyncio.sleep(latency)

        self._live_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_live_streaming(self, chat_id: str) -> None:
        task = self._live_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to the Telegram chat."""
        if self._app is None:
            logger.warning("TelegramChannel: send() called but app is not running")
            return

        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata
        bot: Bot = self._app.bot

        if phase == StreamPhase.START:
            self._buffers[chat_id] = ""
            # New turn — abandon any stale tool-status id (the previous attempt's
            # status, if a retry, stays as-is in the chat history).
            self._tool_msg_ids.pop(chat_id, None)
            # Typing indicator runs in both modes so the dots stay visible during
            # text streaming. Live streaming is layered on top when enabled.
            self._start_typing(chat_id)
            if self._streaming:
                self._start_live_streaming(chat_id)

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start' and self._show_tool_calls:
                # Rolling tool-status: one message that updates as each tool runs.
                # On a new tool_start we edit the existing status (replacing the previous
                # ✅/❌ marker) or post a fresh one if none exists yet.
                name = metadata.get('name', '')
                label = f"⚙️ {name}…"
                existing_id = self._tool_msg_ids.get(chat_id)
                if existing_id is not None:
                    try:
                        await bot.edit_message_text(label, chat_id=int(chat_id), message_id=existing_id)
                    except Exception:
                        try:
                            sent = await bot.send_message(int(chat_id), label)
                            self._tool_msg_ids[chat_id] = sent.message_id
                        except Exception:
                            logger.exception("TelegramChannel: send_message failed (tool_start)")
                else:
                    try:
                        sent = await bot.send_message(int(chat_id), label)
                        self._tool_msg_ids[chat_id] = sent.message_id
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (tool_start)")
                try:
                    await bot.send_chat_action(int(chat_id), ChatAction.TYPING)
                except Exception:
                    pass
            elif kind == 'tool_end' and self._show_tool_calls:
                name = metadata.get('name', '')
                is_error = metadata.get('is_error', False)
                if is_error:
                    result = str(metadata.get('result', ''))
                    label = f"❌ {name}\n{result}" if result else f"❌ {name}"
                else:
                    label = f"✅ {name}"
                existing_id = self._tool_msg_ids.get(chat_id)
                if existing_id is not None:
                    try:
                        await bot.edit_message_text(label, chat_id=int(chat_id), message_id=existing_id)
                    except Exception:
                        pass  # benign edit conflict (e.g. identical text)
                try:
                    await bot.send_chat_action(int(chat_id), ChatAction.TYPING)
                except Exception:
                    pass
            else:
                # text / thinking chunk — if a rolling tool-status message is still up,
                # delete it now: the model is starting to stream its next response, so
                # the prior tool status no longer needs to be visible.
                existing_id = self._tool_msg_ids.pop(chat_id, None)
                if existing_id is not None:
                    try:
                        await bot.delete_message(int(chat_id), existing_id)
                    except Exception:
                        pass
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            reply_params = None
            origin_msg_id = metadata.get('origin_message_id')
            # When keep_typing is set the model hit a non-final stop reason (e.g.
            # tool_calls): flush the buffered text but leave the typing/live-stream
            # task running so the indicator stays on through tool execution.
            keep_typing = metadata.get('keep_typing', False)
            # Final END (stop_reason == Stop) — sweep any leftover tool-status
            # message in case the model finished without streaming text after the
            # last tool.
            if not keep_typing:
                leftover = self._tool_msg_ids.pop(chat_id, None)
                if leftover is not None:
                    try:
                        await bot.delete_message(int(chat_id), leftover)
                    except Exception:
                        pass
            # Auto-reply in groups only — DMs need no disambiguation.
            if self._is_group.get(chat_id, False) and origin_msg_id:
                try:
                    from telegram import ReplyParameters
                    reply_params = ReplyParameters(message_id=int(origin_msg_id))
                except Exception:
                    reply_params = None

            if self._streaming:
                if not keep_typing:
                    self._stop_live_streaming(chat_id)
                    self._stop_typing(chat_id)
                live_msg_id = self._live_msg_ids.pop(chat_id, None)
                if buffered.strip():
                    if live_msg_id is not None:
                        # Edit the live message to its final content (single chunk, no splitting needed for edits)
                        try:
                            await bot.edit_message_text(
                                markdown_to_telegram_html(buffered),
                                chat_id=int(chat_id),
                                message_id=live_msg_id,
                                parse_mode="HTML",
                            )
                        except Exception:
                            logger.exception("TelegramChannel: edit_message_text failed (end)")
                    else:
                        # Nothing was posted yet; send now with reply
                        for chunk in split_message(buffered):
                            try:
                                await bot.send_message(int(chat_id), markdown_to_telegram_html(chunk), parse_mode="HTML", reply_parameters=reply_params)
                            except Exception:
                                logger.exception("TelegramChannel: send_message failed (end, streaming)")
                                try:
                                    await bot.send_message(int(chat_id), chunk, reply_parameters=reply_params)
                                except Exception:
                                    logger.exception("TelegramChannel: send_message fallback failed (end, streaming)")
                if keep_typing:
                    # Next turn re-uses a fresh live message — buffer was just popped.
                    self._buffers[chat_id] = ""
            else:
                if not keep_typing:
                    self._stop_typing(chat_id)
                if buffered.strip():
                    for chunk in split_message(buffered):
                        try:
                            await bot.send_message(int(chat_id), markdown_to_telegram_html(chunk), parse_mode="HTML", reply_parameters=reply_params)
                        except Exception:
                            logger.exception("TelegramChannel: send_message failed (end)")
                            try:
                                await bot.send_message(int(chat_id), chunk, reply_parameters=reply_params)
                            except Exception:
                                logger.exception("TelegramChannel: send_message fallback failed (end)")
                if keep_typing:
                    self._buffers[chat_id] = ""

        elif phase == StreamPhase.ERROR:
            self._stop_typing(chat_id)
            retry_flag = metadata.get('retry', False)
            if retry_flag:
                existing_id = self._retry_msg_ids.get(chat_id)
                if metadata.get('retry_success'):
                    # A later attempt succeeded — quietly delete the rolling status message.
                    if existing_id is not None:
                        try:
                            await bot.delete_message(int(chat_id), existing_id)
                        except Exception:
                            pass
                        self._retry_msg_ids.pop(chat_id, None)
                    return
                text = text_from_parts(msg.parts) or "Unknown error"
                attempt = metadata.get('retry_attempt', 1)
                total = metadata.get('retry_max', 1)
                is_final = metadata.get('retry_final', False)
                if is_final:
                    label = f"❌ {text}" if total <= 1 else f"❌ {text}\n(failed after {total} attempt{'s' if total != 1 else ''})"
                else:
                    label = f"❌ {text}\n⏳ Retrying… ({attempt}/{total})"
                if existing_id is not None:
                    try:
                        await bot.edit_message_text(label, chat_id=int(chat_id), message_id=existing_id)
                    except Exception:
                        # Edit failed (e.g. identical text) — post fresh
                        try:
                            sent = await bot.send_message(int(chat_id), label)
                            self._retry_msg_ids[chat_id] = sent.message_id
                        except Exception:
                            logger.exception("TelegramChannel: send_message failed (retry error)")
                else:
                    try:
                        sent = await bot.send_message(int(chat_id), label)
                        self._retry_msg_ids[chat_id] = sent.message_id
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (retry error)")
                if is_final:
                    self._retry_msg_ids.pop(chat_id, None)
            else:
                text = text_from_parts(msg.parts) or "Unknown error"
                try:
                    await bot.send_message(int(chat_id), f"❌ {text}")
                except Exception:
                    logger.exception("TelegramChannel: send_message failed (error)")

        elif phase is None:
            kind = metadata.get('kind')
            if kind == 'react':
                emoji = metadata.get('emoji', '👍')
                message_id = metadata.get('message_id')
                if message_id is not None:
                    try:
                        from telegram import ReactionTypeEmoji
                        await bot.set_message_reaction(
                            int(chat_id),
                            int(message_id),
                            [ReactionTypeEmoji(emoji)],
                        )
                    except Exception:
                        logger.exception("TelegramChannel: set_message_reaction failed for %r", message_id)
                return

            # Direct send (out-of-band) — handles TTS audio, files, and plain text
            reply_to = metadata.get('reply_to')
            reply_params = None
            if reply_to:
                try:
                    from telegram import ReplyParameters
                    reply_params = ReplyParameters(message_id=int(reply_to))
                except Exception:
                    pass

            for p in msg.parts:
                match p:
                    case AudioPart(audio=audio):
                        try:
                            audio_path = Path(audio)
                            with open(audio_path, 'rb') as f:
                                if audio_path.suffix.lower() == '.ogg':
                                    await bot.send_voice(int(chat_id), InputFile(f, filename='voice.ogg'), reply_parameters=reply_params)
                                else:
                                    await bot.send_audio(int(chat_id), InputFile(f, filename=audio_path.name), reply_parameters=reply_params)
                        except Exception:
                            logger.exception("TelegramChannel: send audio failed for %r", audio)
                    case FilePart(path=fp):
                        try:
                            file_path = Path(fp)
                            caption = text_from_parts(msg.parts) or None
                            with open(file_path, 'rb') as f:
                                await bot.send_document(int(chat_id), InputFile(f, filename=file_path.name), caption=caption, reply_parameters=reply_params)
                        except Exception:
                            logger.exception("TelegramChannel: send_document failed for %r", fp)
                        return  # caption already sent with the document
            text = text_from_parts(msg.parts)
            if text:
                for chunk in split_message(text):
                    try:
                        await bot.send_message(int(chat_id), markdown_to_telegram_html(chunk), parse_mode="HTML", reply_parameters=reply_params)
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (direct)")
                        try:
                            await bot.send_message(int(chat_id), chunk, reply_parameters=reply_params)
                        except Exception:
                            logger.exception("TelegramChannel: send_message fallback failed (direct)")


TelegramBot = TelegramChannel
