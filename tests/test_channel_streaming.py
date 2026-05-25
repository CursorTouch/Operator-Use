"""Tests for live streaming (message edit) mode in Telegram, Discord, and Slack channels."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from program.bus.types import OutgoingMessage, StreamPhase, TextPart


# ── helpers ───────────────────────────────────────────────────────────────────

def _text_msg(chat_id: str, phase: StreamPhase, text: str = "") -> OutgoingMessage:
    parts = [TextPart(text)] if text else []
    return OutgoingMessage(channel="x", chat_id=chat_id, parts=parts, stream_phase=phase)


def _chunk(chat_id: str, text: str) -> OutgoingMessage:
    return OutgoingMessage(
        channel="x", chat_id=chat_id,
        parts=[TextPart(text)], stream_phase=StreamPhase.CHUNK,
    )


def _thinking_chunk(chat_id: str, text: str) -> OutgoingMessage:
    return OutgoingMessage(
        channel="x",
        chat_id=chat_id,
        parts=[TextPart(text)],
        stream_phase=StreamPhase.CHUNK,
        metadata={"kind": "thinking"},
    )


# ── Slack ─────────────────────────────────────────────────────────────────────

class TestSlackStreaming:
    """SlackChannel.send() with streaming=True posts then edits via chat_update."""

    def _channel(self, latency: float = 0.05):
        from program.gateway.channels.slack.service import SlackChannel
        with patch("program.gateway.channels.slack.service._SLACK_AVAILABLE", True):
            ch = SlackChannel.__new__(SlackChannel)
            # bypass __init__ import guard
            from program.gateway.types import BaseChannel
            BaseChannel.__init__(ch)
            ch._bot_token = "tok"
            ch._app_token = "atok"
            ch._commands = []
            ch._command_handler = None
            ch._allow_from = set()
            ch._show_tool_calls = True
            ch._show_thinking = False
            ch._streaming = True
            ch._streaming_latency = latency
            ch._is_group = {}
            ch._buffers = {}
            ch._clients = {}
            ch._thread_ts_map = {}
            ch._handler = None
            ch._live_tasks = {}
            ch._live_ts_map = {}
            ch._retry_ts_map = {}
            ch._tool_ts_map = {}
            ch._prev_tool_ts = {}
            ch._thinking_buffers = {}
            ch._thinking_tasks = {}
        return ch

    @pytest.mark.asyncio
    async def test_streaming_posts_then_updates(self):
        ch = self._channel(latency=0.05)
        chat_id = "C123"

        client = AsyncMock()
        post_resp = {"ts": "msg-ts-1"}
        client.chat_postMessage = AsyncMock(return_value=post_resp)
        client.chat_update = AsyncMock()

        ch._clients[chat_id] = client
        ch._thread_ts_map[chat_id] = None
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "Hello "))
        await ch.send(_chunk(chat_id, "world"))

        # Wait past latency so streaming task fires at least once
        await asyncio.sleep(0.12)

        # The live task should have called chat_postMessage with the buffer
        client.chat_postMessage.assert_called()
        first_call_text = client.chat_postMessage.call_args_list[0].kwargs["text"]
        assert "Hello" in first_call_text or "world" in first_call_text

        # Now END — should call chat_update with final text
        await ch.send(_text_msg(chat_id, StreamPhase.END))
        # chat_update called at least once (streaming task + final END edit)
        assert client.chat_update.called
        final_call = client.chat_update.call_args.kwargs
        assert final_call["ts"] == "msg-ts-1"
        assert "Hello" in final_call["text"] or "world" in final_call["text"]

    @pytest.mark.asyncio
    async def test_streaming_off_uses_post_only(self):
        ch = self._channel(latency=0.05)
        ch._streaming = False
        chat_id = "C456"

        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ts": "ts2"})
        client.chat_update = AsyncMock()

        ch._clients[chat_id] = client
        ch._thread_ts_map[chat_id] = None
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "buffered text"))
        await asyncio.sleep(0.12)  # streaming task shouldn't exist
        assert not client.chat_postMessage.called  # nothing sent yet
        await ch.send(_text_msg(chat_id, StreamPhase.END))

        client.chat_postMessage.assert_called_once()
        client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_no_live_msg_sends_fresh_on_end(self):
        """If streaming=True but buffer is empty during task window, END sends fresh."""
        ch = self._channel(latency=0.5)  # long latency — task won't fire before END
        chat_id = "C789"

        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ts": "ts3"})
        client.chat_update = AsyncMock()

        ch._clients[chat_id] = client
        ch._thread_ts_map[chat_id] = None
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "quick reply"))
        # END before live task fires
        await ch.send(_text_msg(chat_id, StreamPhase.END))

        # No live message was posted, so END falls back to a fresh post
        client.chat_postMessage.assert_called_once()
        client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_thinking_hidden_by_default(self):
        ch = self._channel(latency=0.01)
        chat_id = "C-think"

        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "think-ts"})
        client.chat_update = AsyncMock()

        ch._clients[chat_id] = client
        ch._thread_ts_map[chat_id] = None
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_thinking_chunk(chat_id, "private reasoning"))
        await asyncio.sleep(0.04)

        client.chat_postMessage.assert_not_called()
        assert ch._thinking_buffers == {}


# ── Telegram ──────────────────────────────────────────────────────────────────

class TestTelegramStreaming:
    """TelegramChannel.send() with streaming=True calls send_message then edit_message_text."""

    def _channel(self, latency: float = 0.05):
        from program.gateway.channels.telegram.service import TelegramChannel
        ch = TelegramChannel.__new__(TelegramChannel)
        from program.gateway.types import BaseChannel
        BaseChannel.__init__(ch)
        ch._token = "tok"
        ch._commands = []
        ch._allow_from = set()
        ch._group_policy = "mention"
        ch._show_tool_calls = True
        ch._show_thinking = False
        ch._streaming = True
        ch._streaming_latency = latency
        ch._is_group = {}
        ch._buffers = {}
        ch._app = None
        ch._typing_tasks = {}
        ch._live_tasks = {}
        ch._live_msg_ids = {}
        ch._retry_msg_ids = {}
        ch._tool_msg_ids = {}
        ch._prev_tool_msg_ids = {}
        ch._thinking_buffers = {}
        ch._thinking_tasks = {}
        return ch

    def _mock_app(self):
        bot = AsyncMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 42
        bot.send_message = AsyncMock(return_value=sent_msg)
        bot.edit_message_text = AsyncMock()
        bot.send_chat_action = AsyncMock()
        app = MagicMock()
        app.bot = bot
        return app, bot

    @pytest.mark.asyncio
    async def test_streaming_sends_then_edits(self):
        ch = self._channel(latency=0.05)
        app, bot = self._mock_app()
        ch._app = app
        chat_id = "111"
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "streaming "))
        await ch.send(_chunk(chat_id, "text"))
        await asyncio.sleep(0.12)

        # Live task should have sent the initial message
        bot.send_message.assert_called()
        first_call_text = bot.send_message.call_args_list[0].args[1] if bot.send_message.call_args_list[0].args else bot.send_message.call_args_list[0].kwargs.get("text", "")
        assert "streaming" in first_call_text or "text" in first_call_text

        await ch.send(_text_msg(chat_id, StreamPhase.END))
        assert bot.edit_message_text.called
        call_kwargs = bot.edit_message_text.call_args.kwargs
        assert call_kwargs.get("message_id") == 42

    @pytest.mark.asyncio
    async def test_streaming_off_uses_send_only(self):
        ch = self._channel(latency=0.05)
        ch._streaming = False
        app, bot = self._mock_app()
        ch._app = app
        chat_id = "222"
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "buffered"))
        await asyncio.sleep(0.12)
        assert not bot.send_message.called
        await ch.send(_text_msg(chat_id, StreamPhase.END))

        bot.send_message.assert_called_once()
        bot.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_thinking_hidden_by_default(self):
        ch = self._channel(latency=0.01)
        app, bot = self._mock_app()
        ch._app = app
        chat_id = "333"
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_thinking_chunk(chat_id, "private reasoning"))
        await asyncio.sleep(0.04)

        bot.send_message.assert_not_called()
        assert ch._thinking_buffers == {}


# ── Discord ───────────────────────────────────────────────────────────────────

class TestDiscordStreaming:
    """DiscordChannel.send() with streaming=True sends then edits the live message."""

    def _channel(self, latency: float = 0.05):
        from program.gateway.channels.discord.service import DiscordChannel
        ch = DiscordChannel.__new__(DiscordChannel)
        from program.gateway.types import BaseChannel
        BaseChannel.__init__(ch)
        ch._token = "tok"
        ch._commands = []
        ch._command_handler = None
        ch._commands_synced = False
        ch._allow_from = set()
        ch._group_policy = "mention"
        ch._show_tool_calls = True
        ch._show_thinking = False
        ch._streaming = True
        ch._streaming_latency = latency
        ch._is_group = {}
        ch._buffers = {}
        ch._client = None
        ch._discord_channels = {}
        ch._discord_messages = {}
        ch._typing_tasks = {}
        ch._live_tasks = {}
        ch._live_messages = {}
        ch._retry_messages = {}
        ch._tool_messages = {}
        ch._prev_tool_messages = {}
        ch._thinking_buffers = {}
        ch._thinking_tasks = {}
        return ch

    @pytest.mark.asyncio
    async def test_streaming_sends_then_edits(self):
        ch = self._channel(latency=0.05)
        chat_id = "999"

        live_msg = AsyncMock()
        live_msg.edit = AsyncMock()

        discord_ch = AsyncMock()
        discord_ch.send = AsyncMock(return_value=live_msg)

        ch._discord_channels[chat_id] = discord_ch
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "live "))
        await ch.send(_chunk(chat_id, "update"))
        await asyncio.sleep(0.12)

        discord_ch.send.assert_called()

        await ch.send(_text_msg(chat_id, StreamPhase.END))
        assert live_msg.edit.called

    @pytest.mark.asyncio
    async def test_thinking_hidden_by_default(self):
        ch = self._channel(latency=0.01)
        chat_id = "think-discord"

        discord_ch = AsyncMock()
        discord_ch.send = AsyncMock()
        discord_ch.trigger_typing = AsyncMock()
        ch._discord_channels[chat_id] = discord_ch
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_thinking_chunk(chat_id, "private reasoning"))
        await asyncio.sleep(0.04)

        discord_ch.send.assert_not_called()
        assert ch._thinking_buffers == {}

    @pytest.mark.asyncio
    async def test_streaming_off_sends_fresh_on_end(self):
        ch = self._channel(latency=0.05)
        ch._streaming = False
        chat_id = "888"

        discord_ch = AsyncMock()
        discord_ch.send = AsyncMock()

        ch._discord_channels[chat_id] = discord_ch
        ch._is_group[chat_id] = False

        await ch.send(_text_msg(chat_id, StreamPhase.START))
        await ch.send(_chunk(chat_id, "buffered"))
        await asyncio.sleep(0.12)
        assert not discord_ch.send.called
        await ch.send(_text_msg(chat_id, StreamPhase.END))

        discord_ch.send.assert_called_once()
