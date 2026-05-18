from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel, GatewayEvent

if TYPE_CHECKING:
    from program.gateway.service import Gateway

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
    One channel per Telegram chat.

    Accumulates text chunks and sends a single message when stream_end fires.
    Tool events emit short status messages so the user knows the agent is working.
    """

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._id = f"telegram:{chat_id}"
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
                    await self._send(self._text_buffer)
                    self._text_buffer = ""

            case 'tool_start':
                name = event.data.get('name', '')
                await self._bot.send_message(self._chat_id, f"⚙️ {name}…")

            case 'tool_end':
                if event.data.get('is_error'):
                    result = str(event.data.get('result', ''))[:300]
                    await self._bot.send_message(self._chat_id, f"⚠️ {result}")

            case 'error':
                await self._send(f"❌ {event.data.get('message', 'Unknown error')}")

    async def _send(self, text: str) -> None:
        for i in range(0, len(text), _TELEGRAM_MSG_LIMIT):
            await self._bot.send_message(self._chat_id, text[i:i + _TELEGRAM_MSG_LIMIT])


class TelegramBot:
    """
    Manages a Telegram bot that routes messages through the Gateway.

    Each chat gets its own TelegramChannel registered with the gateway.
    Messages are processed one at a time across all chats (gateway lock).

    Requires: pip install "python-telegram-bot>=20.0"

    Usage:
        bot = TelegramBot(gateway, token=os.environ["TELEGRAM_BOT_TOKEN"])
        await bot.run()   # run as a background task or directly
    """

    def __init__(self, gateway: Gateway, token: str) -> None:
        if not _PTB_AVAILABLE:
            raise ImportError('python-telegram-bot>=20.0 is required for TelegramBot.')
        self._gateway = gateway
        self._token = token
        self._channels: dict[int, TelegramChannel] = {}

    def _get_or_create(self, chat_id: int, bot: Bot) -> TelegramChannel:
        if chat_id not in self._channels:
            ch = TelegramChannel(bot, chat_id)
            self._channels[chat_id] = ch
            self._gateway.register(ch)
        return self._channels[chat_id]

    async def run(self) -> None:
        """Start polling. Runs until the asyncio task is cancelled."""
        app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.message.text:
                return
            chat_id = update.message.chat_id
            text = update.message.text.strip()
            if not text:
                return
            channel = self._get_or_create(chat_id, ctx.bot)
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await self._gateway.send(channel.channel_id, text)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        logger.info("Telegram bot started (polling)")

        try:
            await asyncio.Future()  # run until cancelled
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
