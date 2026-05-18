from __future__ import annotations

import asyncio
import logging

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, text_from_parts

logger = logging.getLogger(__name__)

try:
    from telegram import Bot, Update
    from telegram.constants import ChatAction
    from telegram.ext import Application, ContextTypes, MessageHandler, filters
    _PTB_AVAILABLE = True
except ImportError:
    _PTB_AVAILABLE = False

_TELEGRAM_MSG_LIMIT = 4096


class TelegramChannel(BaseChannel):
    """
    A single Telegram bot that handles all chats.

    One instance = one bot = all chats. Each chat_id gets its own text
    accumulation buffer. Bus and gateway are set externally via register().

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
        """Build PTB app, register handler, start polling. Runs until cancelled."""
        self._app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.message.text:
                return
            chat_id = update.message.chat_id
            text = update.message.text.strip()
            if not text:
                return
            user_id = str(update.message.from_user.id) if update.message.from_user else ""
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            msg = IncomingMessage(
                channel="telegram",
                chat_id=str(chat_id),
                parts=[TextPart(text)],
                user_id=user_id,
            )
            await self.receive(msg)

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

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
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        if self._app is None:
            logger.warning("TelegramChannel: send() called but app is not running")
            return

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
                # text or thinking chunk — accumulate
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
            # Direct send (out-of-band)
            text = text_from_parts(msg.parts)
            if text:
                for i in range(0, len(text), _TELEGRAM_MSG_LIMIT):
                    try:
                        await bot.send_message(int(chat_id), text[i:i + _TELEGRAM_MSG_LIMIT])
                    except Exception:
                        logger.exception("TelegramChannel: send_message failed (direct)")


# Backward compatibility alias
TelegramBot = TelegramChannel
