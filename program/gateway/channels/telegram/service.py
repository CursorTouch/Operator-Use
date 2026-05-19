from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
from program.gateway.channels.telegram.utils import _TELEGRAM_MSG_LIMIT, _MEDIA_DIR, audio_mime_ext

logger = logging.getLogger(__name__)

try:
    from telegram import Bot, InputFile, Update
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

    def __init__(self, token: str) -> None:
        super().__init__()
        if not _PTB_AVAILABLE:
            raise ImportError('python-telegram-bot>=20.0 is required for TelegramChannel.')
        self._token = token
        self._buffers: dict[str, str] = {}
        self._app: Application | None = None

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

            await self.receive(IncomingMessage(
                channel="telegram",
                chat_id=chat_id,
                parts=parts,
                user_id=user_id,
                message_id=str(msg.message_id),
            ))

        self._app.add_handler(MessageHandler(
            (filters.TEXT | filters.VOICE | filters.AUDIO) & ~filters.COMMAND,
            _on_message,
        ))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started (polling)")

        try:
            await asyncio.Future()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

    async def disconnect(self) -> None:
        """Stop PTB app and release resources."""
        if self._app is not None:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.exception("TelegramChannel: error during disconnect")
            self._app = None

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

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start':
                name = metadata.get('name', '')
                try:
                    await bot.send_message(int(chat_id), f"⚙️ {name}…")
                except Exception:
                    logger.exception("TelegramChannel: send_message failed (tool_start)")
            elif kind == 'tool_end' and metadata.get('is_error'):
                result = str(metadata.get('result', ''))
                try:
                    await bot.send_message(int(chat_id), f"⚠️ {result}")
                except Exception:
                    logger.exception("TelegramChannel: send_message failed (tool_end)")
            else:
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, "")
            if buffered.strip():
                for i in range(0, len(buffered), _TELEGRAM_MSG_LIMIT):
                    try:
                        await bot.send_message(int(chat_id), buffered[i:i + _TELEGRAM_MSG_LIMIT])
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (end)")

        elif phase == StreamPhase.ERROR:
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
                for i in range(0, len(text), _TELEGRAM_MSG_LIMIT):
                    try:
                        await bot.send_message(int(chat_id), text[i:i + _TELEGRAM_MSG_LIMIT], reply_parameters=reply_params)
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (direct)")


TelegramBot = TelegramChannel
