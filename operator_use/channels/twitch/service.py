from __future__ import annotations

import asyncio
import logging

from operator_use.gateway.types import BaseChannel
from operator_use.channels.shutdown import quiet_library_logging
from operator_use.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, text_from_parts
from operator_use.channels.twitch.utils import split_message

logger = logging.getLogger(__name__)

try:
    import twitchio
    from twitchio.ext import commands as twitch_commands
    _TWITCHIO_AVAILABLE = True
except ImportError:
    _TWITCHIO_AVAILABLE = False


class TwitchChannel(BaseChannel):
    """
    One channel for the entire Twitch chat room.

    connect() connects to Twitch IRC and runs until cancelled.
    Accumulates text chunks and sends the full response when END fires,
    splitting into ≤500 character messages as required by Twitch.

    Requires: pip install "twitchio>=2.0"

    channel_id = f"twitch:{channel_name}"
    """

    def __init__(
        self,
        token: str,
        nick: str,
        channel_name: str,
        name: str | None = None,
        prefix: str = "!",
        allow_from: list[str] | None = None,
        gateway=None,  # legacy param, ignored
    ) -> None:
        super().__init__()
        if not _TWITCHIO_AVAILABLE:
            raise ImportError('twitchio>=2.0 is required for TwitchChannel.')
        self._token = token
        self._nick = nick
        self._twitch_channel_name = channel_name.lstrip('#')
        self._name = name  # custom channel_id override (e.g. "alice:twitch")
        self._prefix = prefix
        self._allow_from = [u.lower() for u in (allow_from or [])]
        self._buffer: str = ""
        self._bot_ref: _TwitchBotImpl | None = None

    @property
    def channel_id(self) -> str:
        return self._name if self._name else f"twitch:{self._twitch_channel_name}"

    async def connect(self) -> None:
        """Connect to Twitch IRC and serve. Runs until the asyncio task is cancelled."""
        bot = _TwitchBotImpl(
            operator_channel=self,
            token=self._token,
            nick=self._nick,
            channel_name=self._twitch_channel_name,
            prefix=self._prefix,
            allow_from=self._allow_from,
        )
        self._bot_ref = bot
        try:
            await bot.start()
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await bot.close()
            except Exception:
                pass

    async def disconnect(self) -> None:
        """Stop the Twitch bot connection."""
        if self._bot_ref is not None:
            with quiet_library_logging("twitchio", "aiohttp", "asyncio") as debug:
                try:
                    await self._bot_ref.close()
                except Exception:
                    if debug:
                        logger.exception("TwitchChannel: error during disconnect")
            self._bot_ref = None

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to the Twitch chat room."""
        phase = msg.stream_phase
        metadata = msg.metadata

        if phase == StreamPhase.START:
            self._buffer = ""

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'tool_start':
                await self._send_raw(f"⚙️ {metadata.get('display_name', '') or metadata.get('name', '')}…")
            elif kind == 'tool_end' and metadata.get('is_error'):
                await self._send_raw(f"⚠️ {metadata.get('result', '')}")
            else:
                self._buffer += text_from_parts(msg.parts)

        elif phase == StreamPhase.END:
            if self._buffer.strip():
                await self._send_raw(self._buffer)
            self._buffer = ""

        elif phase == StreamPhase.ERROR:
            await self._send_raw(f"❌ {text_from_parts(msg.parts) or 'Unknown error'}")
            self._buffer = ""

        elif phase is None:
            text = text_from_parts(msg.parts)
            if text:
                await self._send_raw(text)

    async def _send_raw(self, text: str) -> None:
        if not self._bot_ref:
            return
        twitch_ch = self._bot_ref.get_channel(self._twitch_channel_name)
        if not twitch_ch:
            logger.warning("TwitchChannel: channel %r not found in bot", self._twitch_channel_name)
            return
        for chunk in split_message(text):
            try:
                await twitch_ch.send(chunk)
            except Exception:
                logger.exception("TwitchChannel: send failed")


class _TwitchBotImpl(twitch_commands.Bot if _TWITCHIO_AVAILABLE else object):
    """Internal twitchio Bot that routes messages to a TwitchChannel."""

    def __init__(
        self,
        operator_channel: TwitchChannel,
        token: str,
        nick: str,
        channel_name: str,
        prefix: str,
        allow_from: list[str],
    ) -> None:
        super().__init__(
            token=token,
            nick=nick,
            prefix=prefix,
            initial_channels=[channel_name],
        )
        self._operator_channel = operator_channel
        # Strong refs to fire-and-forget receive tasks: the loop only holds weak
        # refs, so without this a task can be GC'd mid-flight.
        self._tasks: set[asyncio.Task] = set()
        self._allow_from = allow_from

    async def event_ready(self) -> None:
        logger.info("Twitch bot connected as %s", self.nick)

    async def event_message(self, message: twitchio.Message) -> None:
        if message.echo:
            return
        author = message.author.name if message.author else ""
        if self._allow_from and author not in self._allow_from:
            return
        text = (message.content or "").strip()
        if not text:
            return
        task = asyncio.create_task(self._operator_channel.receive(IncomingMessage(
            channel=self._operator_channel.channel_id,
            chat_id=self._operator_channel.channel_id,
            parts=[TextPart(text)],
            user_id=author,
        )))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def event_error(self, error: Exception, data: str = "") -> None:
        logger.error("Twitch bot error: %s", error, exc_info=True)
