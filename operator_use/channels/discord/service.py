from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from operator_use.gateway.types import BaseChannel
from operator_use.channels.shutdown import quiet_library_logging
from operator_use.channels.shared import build_retry_label, format_thinking_label, COMMAND_NAME_RE
from operator_use.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, AudioPart, FilePart, text_from_parts
from operator_use.commands.types import CommandParseResult
from operator_use.channels.discord.utils import _MEDIA_DIR, is_audio_attachment, split_message

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

    def __init__(
        self,
        token: str,
        name: str = "discord",
        commands: list[tuple[str, str]] | None = None,
        command_handler: Callable[[CommandParseResult], Awaitable[str]] | None = None,
        allow_from: list[str] | None = None,
        group_policy: str = "mention",
        show_tool_calls: bool = True,
        show_thinking: bool = False,
        streaming: bool = True,
        streaming_latency: float = 1.0,
    ) -> None:
        super().__init__()
        if not _DISCORD_AVAILABLE:
            raise ImportError('discord.py>=2.0 is required for DiscordChannel.')
        self._name = name
        self._token = token
        self._commands = commands or []
        self._command_handler = command_handler
        self._commands_synced = False
        self._allow_from = set(allow_from or [])
        self._group_policy = group_policy
        self._show_tool_calls = show_tool_calls
        self._show_thinking = show_thinking
        self._streaming = streaming
        self._streaming_latency = streaming_latency
        self._is_group: dict[str, bool] = {}  # chat_id → True if guild channel (not DM)
        self._buffers: dict[str, str] = {}
        self._client: discord.Client | None = None
        self._discord_channels: dict[str, discord.abc.Messageable] = {}
        self._discord_messages: dict[str, discord.Message] = {}
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._live_tasks: dict[str, asyncio.Task] = {}
        self._live_messages: dict[str, discord.Message | None] = {}
        self._retry_messages: dict[str, discord.Message] = {}      # chat_id → rolling retry-status message
        self._tool_messages: dict[str, discord.Message] = {}       # chat_id → rolling tool/thinking-status message (shared slot)
        self._prev_tool_messages: dict[str, discord.Message] = {}  # chat_id → tool-status msg from previous failed attempt
        self._thinking_buffers: dict[str, str] = {}                # chat_id → accumulated thinking text
        self._thinking_tasks: dict[str, asyncio.Task] = {}         # chat_id → debounced thinking-stream task

    @property
    def channel_id(self) -> str:
        return self._name

    async def connect(self) -> None:
        """Create Discord client, register events, connect. Runs until cancelled."""
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        client = self._client
        assert client is not None
        tree = discord.app_commands.CommandTree(client)
        self._register_application_commands(tree)

        @client.event
        async def on_ready() -> None:
            logger.info("Discord bot logged in as %s", client.user)
            if self._commands_synced:
                return
            try:
                synced = await tree.sync()
                self._commands_synced = True
                logger.info("Discord application commands synced: %d", len(synced))
            except Exception:
                logger.exception("DiscordChannel: failed to sync application commands")

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mentioned = client.user in message.mentions if client.user else False
            if self._group_policy == "mention" and not is_dm and not is_mentioned:
                return

            user_id = str(message.author.id)
            if self._allow_from and user_id not in self._allow_from:
                return

            chat_id = str(message.channel.id)
            self._discord_channels[chat_id] = message.channel
            self._discord_messages[chat_id] = message
            self._is_group[chat_id] = not is_dm

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

            meta: dict = {}
            ref = getattr(message, 'reference', None)
            if ref is not None and getattr(ref, 'message_id', None):
                meta['reply_to'] = str(ref.message_id)

            await self.receive(IncomingMessage(
                channel=self._name,
                chat_id=chat_id,
                parts=parts,
                user_id=user_id,
                message_id=str(message.id),
                metadata=meta,
            ))

        try:
            await client.start(self._token)
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()

    def _valid_commands(self) -> list[tuple[str, str]]:
        return [
            (name, (desc or name)[:100])
            for name, desc in self._commands
            if COMMAND_NAME_RE.fullmatch(name)
        ]

    def _register_application_commands(self, tree) -> None:
        if self._command_handler is None:
            return

        for name, description in self._valid_commands():
            def _make_callback(command_name: str):
                async def _callback(interaction: discord.Interaction, args: str = "") -> None:
                    user_id = str(interaction.user.id) if getattr(interaction, "user", None) is not None else ""
                    if self._allow_from and user_id not in self._allow_from:
                        await interaction.response.send_message("You are not allowed to use this bot.", ephemeral=True)
                        return

                    await interaction.response.defer(thinking=True)
                    parsed = CommandParseResult(
                        name=command_name,
                        args=args.split() if args else [],
                        raw=f"/{command_name} {args}".strip(),
                    )
                    try:
                        output = await self._command_handler(parsed) if self._command_handler is not None else ""
                    except Exception:
                        logger.exception("DiscordChannel: slash command failed: /%s", command_name)
                        output = f"Command failed: /{command_name}"

                    if output:
                        for chunk in split_message(output):
                            await interaction.followup.send(chunk)
                    else:
                        try:
                            await interaction.delete_original_response()
                        except Exception:
                            pass

                _callback.__annotations__ = {
                    "interaction": discord.Interaction,
                    "args": str,
                    "return": None,
                }
                return discord.app_commands.describe(
                    args="Arguments for this Operator command."
                )(_callback)

            command = discord.app_commands.Command(
                name=name,
                description=description,
                callback=_make_callback(name),
            )
            tree.add_command(command)

    async def disconnect(self) -> None:
        """Close the Discord client."""
        if self._client is not None:
            with quiet_library_logging("discord", "aiohttp", "asyncio") as debug:
                try:
                    await self._client.close()
                except Exception:
                    if debug:
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
                        await ch.trigger_typing()  # pyright: ignore[reportAttributeAccessIssue]
                    except Exception:
                        pass
                await asyncio.sleep(8)

        self._typing_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    def _start_live_streaming(self, chat_id: str) -> None:
        self._stop_live_streaming(chat_id)
        self._live_messages[chat_id] = None
        latency = self._streaming_latency

        async def _loop() -> None:
            await asyncio.sleep(latency)
            while True:
                buffered = self._buffers.get(chat_id, "")
                discord_ch = self._discord_channels.get(chat_id)
                if buffered.strip() and discord_ch is not None:
                    existing = self._live_messages.get(chat_id)
                    if existing is None:
                        try:
                            sent = await discord_ch.send(buffered)
                            self._live_messages[chat_id] = sent
                        except Exception:
                            logger.exception("DiscordChannel: live send failed")
                        # Posting a real message clears the client-side typing
                        # indicator; re-trigger so it stays visible while streaming.
                        try:
                            await discord_ch.trigger_typing()  # pyright: ignore[reportAttributeAccessIssue]
                        except Exception:
                            pass
                    else:
                        try:
                            await existing.edit(content=buffered)
                        except Exception:
                            pass  # edit conflicts during rapid chunks are benign
                await asyncio.sleep(latency)

        self._live_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_live_streaming(self, chat_id: str) -> None:
        task = self._live_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    def _start_thinking_stream(self, chat_id: str) -> None:
        self._stop_thinking_stream(chat_id)
        latency = self._streaming_latency

        async def _loop() -> None:
            await asyncio.sleep(latency)
            while True:
                buffered = self._thinking_buffers.get(chat_id, "")
                discord_ch = self._discord_channels.get(chat_id)
                if buffered.strip() and discord_ch is not None:
                    label = format_thinking_label(buffered)
                    existing = self._tool_messages.get(chat_id)
                    if existing is not None:
                        try:
                            await existing.edit(content=label)
                        except Exception:
                            pass
                    else:
                        try:
                            sent = await discord_ch.send(label)
                            self._tool_messages[chat_id] = sent
                            await discord_ch.trigger_typing()  # pyright: ignore[reportAttributeAccessIssue]
                        except Exception:
                            logger.exception("DiscordChannel: send failed (thinking)")
                await asyncio.sleep(latency)

        self._thinking_tasks[chat_id] = asyncio.create_task(_loop())

    def _stop_thinking_stream(self, chat_id: str) -> None:
        task = self._thinking_tasks.pop(chat_id, None)
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
            self._thinking_buffers.pop(chat_id, None)
            self._stop_thinking_stream(chat_id)
            # Save the stale tool-status handle so retry_success can delete it.
            stale = self._tool_messages.pop(chat_id, None)
            if stale is not None:
                self._prev_tool_messages[chat_id] = stale
            # Typing indicator runs in both modes so the dots stay visible during
            # text streaming. Live streaming is layered on top when enabled.
            self._start_typing(chat_id)
            if self._streaming:
                self._start_live_streaming(chat_id)

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind == 'thinking':
                if not self._show_thinking:
                    return
                chunk = text_from_parts(msg.parts)
                self._thinking_buffers[chat_id] = self._thinking_buffers.get(chat_id, "") + chunk
                if chat_id not in self._thinking_tasks:
                    self._start_thinking_stream(chat_id)
            elif kind == 'tool_start' and self._show_tool_calls:
                self._stop_thinking_stream(chat_id)
                self._thinking_buffers.pop(chat_id, None)
                # Rolling tool-status: one message that updates as each tool runs.
                name = metadata.get('display_name', '') or metadata.get('name', '')
                label = f"⚙️ `{name}`…"
                existing = self._tool_messages.get(chat_id)
                if existing is not None:
                    try:
                        await existing.edit(content=label)
                    except Exception:
                        if discord_ch is not None:
                            try:
                                sent = await discord_ch.send(label)
                                self._tool_messages[chat_id] = sent
                            except Exception:
                                logger.exception("DiscordChannel: send failed (tool_start)")
                elif discord_ch is not None:
                    try:
                        sent = await discord_ch.send(label)
                        self._tool_messages[chat_id] = sent
                    except Exception:
                        logger.exception("DiscordChannel: send failed (tool_start)")
                if discord_ch is not None:
                    try:
                        await discord_ch.trigger_typing()  # pyright: ignore[reportAttributeAccessIssue]
                    except Exception:
                        pass
            elif kind == 'tool_update' and self._show_tool_calls:
                text = metadata.get('text', '')
                if text:
                    existing = self._tool_messages.get(chat_id)
                    if existing is not None:
                        try:
                            await existing.edit(content=text)
                        except Exception:
                            pass
            elif kind == 'tool_end' and self._show_tool_calls:
                name = metadata.get('display_name', '') or metadata.get('name', '')
                is_error = metadata.get('is_error', False)
                if is_error:
                    result = str(metadata.get('result', ''))
                    label = f"❌ `{name}`\n{result}" if result else f"❌ `{name}`"
                else:
                    label = f"✅ `{name}`"
                existing = self._tool_messages.get(chat_id)
                if existing is not None:
                    try:
                        await existing.edit(content=label)
                    except Exception:
                        pass
                if discord_ch is not None:
                    try:
                        await discord_ch.trigger_typing()  # pyright: ignore[reportAttributeAccessIssue]
                    except Exception:
                        pass
            else:
                # text chunk — stop thinking stream and delete the rolling status message.
                self._stop_thinking_stream(chat_id)
                self._thinking_buffers.pop(chat_id, None)
                existing = self._tool_messages.pop(chat_id, None)
                if existing is not None:
                    try:
                        await existing.delete()
                    except Exception:
                        pass
                text = text_from_parts(msg.parts)
                self._buffers[chat_id] = self._buffers.get(chat_id, "") + text

        elif phase == StreamPhase.END:
            self._stop_thinking_stream(chat_id)
            self._thinking_buffers.pop(chat_id, None)
            buffered = self._buffers.pop(chat_id, "")
            reference = None
            origin_msg_id = metadata.get('origin_message_id')
            keep_typing = metadata.get('keep_typing', False)
            suppress_text = metadata.get('suppress_text', False)

            # TTS will deliver audio — discard buffered text and do cleanup only.
            if suppress_text and not keep_typing:
                leftover = self._tool_messages.pop(chat_id, None)
                if leftover is not None:
                    try:
                        await leftover.delete()
                    except Exception:
                        pass
                if self._streaming:
                    self._stop_live_streaming(chat_id)
                    live_msg = self._live_messages.pop(chat_id, None)
                    if live_msg is not None:
                        try:
                            await live_msg.delete()
                        except Exception:
                            pass
                return

            # Final END — sweep any leftover tool-status message (model ended
            # without streaming text after the last tool).
            if not keep_typing:
                leftover = self._tool_messages.pop(chat_id, None)
                if leftover is not None:
                    try:
                        await leftover.delete()
                    except Exception:
                        pass
            # Auto-reply in guild channels only — DMs need no disambiguation.
            if self._is_group.get(chat_id, False) and origin_msg_id and discord_ch is not None:
                try:
                    reference = discord.MessageReference(
                        message_id=int(origin_msg_id),
                        channel_id=int(chat_id),
                        fail_if_not_exists=False,
                    )
                except Exception:
                    reference = None

            if self._streaming:
                if not keep_typing:
                    self._stop_live_streaming(chat_id)
                    self._stop_typing(chat_id)
                live_msg = self._live_messages.pop(chat_id, None)
                if buffered.strip() and discord_ch is not None:
                    if live_msg is not None:
                        try:
                            await live_msg.edit(content=buffered)
                        except Exception:
                            logger.exception("DiscordChannel: edit failed (end)")
                    else:
                        for i, chunk in enumerate(split_message(buffered)):
                            try:
                                if i == 0 and reference is not None:
                                    await discord_ch.send(chunk, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                                else:
                                    await discord_ch.send(chunk)
                            except Exception:
                                logger.exception("DiscordChannel: send failed (end, streaming)")
                if keep_typing:
                    self._buffers[chat_id] = ""
            else:
                if not keep_typing:
                    self._stop_typing(chat_id)
                if buffered.strip() and discord_ch is not None:
                    for i, chunk in enumerate(split_message(buffered)):
                        try:
                            if i == 0 and reference is not None:
                                await discord_ch.send(chunk, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                            else:
                                await discord_ch.send(chunk)
                        except Exception:
                            logger.exception("DiscordChannel: send failed (end)")
                if keep_typing:
                    self._buffers[chat_id] = ""

        elif phase == StreamPhase.ERROR:
            self._stop_typing(chat_id)
            retry_flag = metadata.get('retry', False)
            if retry_flag:
                existing = self._retry_messages.get(chat_id)
                if metadata.get('retry_success'):
                    if existing is not None:
                        try:
                            await existing.delete()
                        except Exception:
                            pass
                        self._retry_messages.pop(chat_id, None)
                    prev_tool = self._prev_tool_messages.pop(chat_id, None)
                    if prev_tool is not None:
                        try:
                            await prev_tool.delete()
                        except Exception:
                            pass
                    return
                text = text_from_parts(msg.parts) or "Unknown error"
                attempt = metadata.get('retry_attempt', 1)
                total = metadata.get('retry_max', 1)
                is_final = metadata.get('retry_final', False)
                label = build_retry_label(text, attempt, total, is_final)
                if existing is not None:
                    try:
                        await existing.edit(content=label)
                    except Exception:
                        if discord_ch is not None:
                            try:
                                sent = await discord_ch.send(label)
                                self._retry_messages[chat_id] = sent
                            except Exception:
                                logger.exception("DiscordChannel: send failed (retry error)")
                elif discord_ch is not None:
                    try:
                        sent = await discord_ch.send(label)
                        self._retry_messages[chat_id] = sent
                    except Exception:
                        logger.exception("DiscordChannel: send failed (retry error)")
                if is_final:
                    self._retry_messages.pop(chat_id, None)
            elif discord_ch is not None:
                text = text_from_parts(msg.parts) or "Unknown error"
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
                    for i, chunk in enumerate(split_message(text)):
                        try:
                            if i == 0 and reference is not None:
                                await discord_ch.send(chunk, reference=reference)  # type: ignore[reportCallIssue,reportArgumentType]
                            else:
                                await discord_ch.send(chunk)
                        except Exception:
                            logger.exception("DiscordChannel: send failed (direct)")


DiscordBot = DiscordChannel
