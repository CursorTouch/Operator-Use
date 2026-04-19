"""Slack channel using Socket Mode or Webhooks."""

import asyncio
import logging
import re
import hmac
import hashlib
from pathlib import Path
from typing import Optional

import aiohttp.web
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web.async_client import AsyncWebClient

from operator_use.bus.views import (
    IncomingMessage,
    OutgoingMessage,
    TextPart,
    text_from_parts,
)
from operator_use.gateway.channels.config import SlackConfig
from operator_use.gateway.channels.base import BaseChannel


logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000  # Slack message character limit (practical)

_EMOJI_TO_SLACK: dict[str, str] = {
    "👍": "thumbsup",
    "👎": "thumbsdown",
    "❤️": "heart",
    "🔥": "fire",
    "😂": "joy",
    "😊": "blush",
    "😍": "heart_eyes",
    "🎉": "tada",
    "✅": "white_check_mark",
    "❌": "x",
    "⚠️": "warning",
    "🚀": "rocket",
    "👀": "eyes",
    "💯": "100",
    "🙏": "pray",
    "😎": "sunglasses",
    "🤔": "thinking_face",
    "😅": "sweat_smile",
    "🥳": "partying_face",
    "👋": "wave",
    "💪": "muscle",
    "🎯": "dart",
    "⭐": "star",
}


def _emoji_to_slack_name(emoji: str) -> str:
    """Convert an emoji character to a Slack reaction name."""
    name = _EMOJI_TO_SLACK.get(emoji)
    if name:
        return name
    # Strip colons if already in :name: format
    if emoji.startswith(":") and emoji.endswith(":"):
        return emoji[1:-1]
    return None  # unknown emoji, skip reaction


def _markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert markdown to Slack mrkdwn format."""
    if not text:
        return ""

    # 1. Protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # 2. Protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Headers -> just the text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Blockquotes -> just the text
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Bold **text** -> *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)

    # 6. Italic _text_ -> _text_ (already in mrkdwn format)

    # 7. Strikethrough ~~text~~ -> ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # 8. Links [text](url) -> <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # 9. Bullet lists - item -> • item
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 10. Restore inline code
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"`{code}`")

    # 11. Restore code blocks
    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"```\n{code}\n```")

    return text


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


def _verify_slack_signature(
    request_body: bytes, signing_secret: str, timestamp: str, signature: str
) -> bool:
    """Verify Slack request signature for webhook security."""
    if not signing_secret:
        return False

    base_string = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    my_signature = (
        "v0=" + hmac.new(signing_secret.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    )

    return hmac.compare_digest(my_signature, signature)


class SlackChannel(BaseChannel):
    """Slack channel using Socket Mode."""

    def __init__(self, config: SlackConfig, bus=None) -> None:
        super().__init__(config, bus)
        self.config = config
        self._socket_client: Optional[SocketModeClient] = None
        self._web_client: Optional[AsyncWebClient] = None
        self._bot_user_id: Optional[str] = None
        self._socket_stop = asyncio.Event()
        self._webhook_runner: Optional[aiohttp.web.AppRunner] = None
        self._webhook_site: Optional[aiohttp.web.TCPSite] = None
        self._webhook_stop = asyncio.Event()
        self._media_dir = Path.home() / ".operator" / "media"

    # _cfg inherited from BaseChannel

    @property
    def name(self) -> str:
        return "slack"

    @property
    def use_webhook(self) -> bool:
        return bool(self._cfg("use_webhook"))

    async def start(self) -> None:
        """Start Slack bot (Socket Mode or Webhook mode)."""
        bot_token = self._cfg("bot_token") or ""

        if not bot_token:
            logger.warning("Slack bot_token not configured, skipping")
            return

        try:
            self._web_client = AsyncWebClient(token=bot_token)

            # Get bot user ID
            try:
                auth = await self._web_client.auth_test()
                self._bot_user_id = auth.get("user_id")
                logger.info(f"Slack bot connected as {self._bot_user_id}")
            except Exception as e:
                logger.warning(f"Slack auth_test failed: {e}")

            if self.use_webhook:
                await self._listen_webhook()
            else:
                # Socket Mode (default)
                app_token = self._cfg("app_token") or ""
                if not app_token:
                    logger.warning("Slack app_token not configured for Socket Mode, skipping")
                    return

                self._socket_client = SocketModeClient(
                    app_token=app_token,
                    web_client=self._web_client,
                )
                await self._listen()
        except Exception as e:
            logger.error(f"Failed to start Slack bot: {e}")

    async def stop(self) -> None:
        """Stop Slack bot (Socket Mode or Webhook mode)."""
        self._socket_stop.set()
        self._webhook_stop.set()

        if self._socket_client:
            try:
                await self._socket_client.close()
            except Exception as e:
                logger.debug(f"Slack socket close failed: {e}")
            self._socket_client = None

        if self._webhook_site:
            await self._webhook_site.stop()
            self._webhook_site = None
        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None

        if self._web_client:
            try:
                await self._web_client.close()
            except Exception as e:
                logger.debug(f"Slack web client close failed: {e}")
            self._web_client = None

    async def _listen_webhook(self) -> None:
        """Run Slack webhook server for receiving events."""
        webhook_url = self._cfg("webhook_url") or ""
        webhook_path = self._cfg("webhook_path") or "/slack"
        webhook_port = int(self._cfg("webhook_port") or 8080)
        signing_secret = self._cfg("signing_secret") or ""

        if not webhook_url:
            logger.warning("Slack use_webhook=True but webhook_url not set, skipping")
            return

        async def handle_slack_event(request: aiohttp.web.Request) -> aiohttp.web.Response:
            """Handle Slack events via HTTP."""
            try:
                # Verify request signature
                timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
                signature = request.headers.get("X-Slack-Signature", "")

                if not timestamp or not signature:
                    logger.warning("Slack webhook: missing signature headers")
                    return aiohttp.web.Response(status=401)

                # Check timestamp is recent (within 5 minutes)
                import time

                current_time = int(time.time())
                try:
                    req_time = int(timestamp)
                    if abs(current_time - req_time) > 300:
                        logger.warning("Slack webhook: request timestamp too old")
                        return aiohttp.web.Response(status=401)
                except ValueError:
                    return aiohttp.web.Response(status=401)

                # Get and verify signature
                body = await request.read()
                if not _verify_slack_signature(body, signing_secret, timestamp, signature):
                    logger.warning("Slack webhook: invalid signature")
                    return aiohttp.web.Response(status=401)

                data = await request.json()

                # Handle URL verification challenge
                if data.get("type") == "url_verification":
                    return aiohttp.web.json_response({"challenge": data.get("challenge")})

                # Handle events
                if data.get("type") == "event_callback":
                    event = data.get("event", {})
                    await self._handle_webhook_event(event)

                return aiohttp.web.Response(status=200)

            except Exception as e:
                logger.error(f"Slack webhook error: {e}")
                return aiohttp.web.Response(status=500)

        # Set up webhook server
        app = aiohttp.web.Application()
        app.router.add_post(webhook_path, handle_slack_event)
        self._webhook_runner = aiohttp.web.AppRunner(app)
        await self._webhook_runner.setup()
        self._webhook_site = aiohttp.web.TCPSite(self._webhook_runner, "0.0.0.0", webhook_port)
        await self._webhook_site.start()
        logger.info(f"Slack webhook server listening on port {webhook_port}")

        try:
            await asyncio.wait_for(self._webhook_stop.wait(), timeout=None)
        except asyncio.CancelledError:
            pass

    async def _on_reaction(self, event: dict, removed: bool) -> None:
        """Handle reaction_added / reaction_removed — forward to agent as a reaction event."""
        user_id = event.get("user")
        # Ignore the bot's own reactions (triggered when agent uses react_message tool)
        if user_id and user_id == self._bot_user_id:
            return
        item = event.get("item", {})
        if item.get("type") != "message":
            return
        channel_id = item.get("channel")
        message_ts = item.get("ts")  # Slack message_id is its timestamp string
        if not channel_id or not message_ts:
            return
        emoji_str = f":{event.get('reaction', '')}:"
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=channel_id,
            parts=[TextPart(content=f"[reaction:{emoji_str}]")],
            user_id=user_id or "",
            account_id=self._cfg("account_id") or "",
            metadata={
                "_reaction_event": True,
                "_reaction_emojis": [] if removed else [emoji_str],
                "_reaction_removed_emojis": [emoji_str] if removed else [],
                "_reaction_bot_message_id": message_ts,
                "user_id": user_id,
            },
        )
        await self.receive(incoming)

    async def _handle_webhook_event(self, event: dict) -> None:
        """Process Slack event from webhook."""
        event_type = event.get("type")
        if event_type in ("reaction_added", "reaction_removed"):
            await self._on_reaction(event, removed=(event_type == "reaction_removed"))
            return
        if event_type != "message":
            return

        # Extract message info
        sender_id = event.get("user")
        channel_id = event.get("channel")
        text = event.get("text", "")
        channel_type = event.get("channel_type", "")

        # Only process DMs (direct messages have type "im")
        if channel_type != "im":
            return

        # Ignore bot messages
        if not sender_id or sender_id == self._bot_user_id:
            return

        # Ignore subtypes (edits, deletions)
        if event.get("subtype"):
            return

        if not self._is_user_allowed(sender_id):
            return

        # Build metadata with Slack-specific info
        metadata = {
            "message_id": event.get("ts"),
            "channel_id": channel_id,
            "user_id": sender_id,
        }

        # Session control commands
        if text:
            _parts = text.strip().split(maxsplit=1)
            _cmd_word = _parts[0].lower() if _parts else ""
            if _cmd_word in ("/start", "/stop", "/restart"):
                command = _cmd_word[1:]
                command_args = _parts[1] if len(_parts) > 1 else ""
                incoming = IncomingMessage(
                    channel=self.name,
                    chat_id=channel_id,
                    parts=[TextPart(content=text.strip())],
                    user_id=sender_id,
                    account_id=self._cfg("account_id") or "",
                    metadata={**metadata, "_command": command, "_command_args": command_args},
                )
                await self.receive(incoming)
                return

        # Create incoming message
        incoming = IncomingMessage(
            channel=self.name,
            chat_id=channel_id,
            parts=[TextPart(content=text)] if text else [],
            user_id=sender_id,
            account_id=self._cfg("account_id") or "",
            metadata=metadata,
        )

        await self.receive(incoming)

    async def _listen(self) -> None:
        """Listen for Slack Socket Mode events."""
        if not self._socket_client:
            return

        try:
            logger.info("Slack Socket Mode connecting...")
            self._socket_client.socket_mode_request_listeners.append(self._on_socket_request)
            await self._socket_client.connect()
            logger.info("Slack Socket Mode connected")
            await self._socket_stop.wait()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Slack listener error: {e}")

    async def _on_socket_request(self, client, req: SocketModeRequest):
        """Handle incoming Slack Socket Mode requests (DM mode only)."""
        from slack_sdk.socket_mode.response import SocketModeResponse

        # Acknowledge immediately
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        try:
            if req.type != "events_api":
                return

            payload = req.payload
            event = payload.get("event", {}) if isinstance(payload, dict) else {}
            if not event:
                return

            event_type = event.get("type")
            if event_type in ("reaction_added", "reaction_removed"):
                await self._on_reaction(event, removed=(event_type == "reaction_removed"))
                return
            if event_type != "message":
                return

            # Extract message info
            sender_id = event.get("user")
            channel_id = event.get("channel")
            text = event.get("text", "")
            channel_type = event.get("channel_type", "")

            # Only process DMs
            if channel_type != "im":
                return

            # Ignore bot messages
            if not sender_id or sender_id == self._bot_user_id:
                return

            # Ignore subtypes (edits, deletions)
            if event.get("subtype"):
                return

            if not self._is_user_allowed(sender_id):
                return

            metadata = {
                "message_id": event.get("ts"),
                "channel_id": channel_id,
                "user_id": sender_id,
            }

            # Session control commands
            if text and text.strip().lower() in ("/start", "/stop", "/restart"):
                command = text.strip()[1:].lower()
                incoming = IncomingMessage(
                    channel=self.name,
                    chat_id=channel_id,
                    parts=[TextPart(content=text.strip())],
                    user_id=sender_id,
                    account_id=self._cfg("account_id") or "",
                    metadata={**metadata, "_command": command},
                )
                await self.receive(incoming)
                return

            incoming = IncomingMessage(
                channel=self.name,
                chat_id=channel_id,
                parts=[TextPart(content=text)] if text else [],
                user_id=sender_id,
                account_id=self._cfg("account_id") or "",
                metadata=metadata,
            )

            await self.receive(incoming)

        except Exception as e:
            logger.error(f"Error handling Slack message: {e}")

    async def send(self, message: OutgoingMessage) -> int | None:
        """Send an outgoing message to Slack."""
        from operator_use.bus.views import StreamPhase

        if not self._web_client:
            logger.warning("Slack client not running, cannot send")
            return None

        try:
            channel_id = message.chat_id

            # Handle emoji reactions
            if message.metadata.get("_reaction"):
                emoji = message.metadata.get("_reaction_emoji", "")
                react_msg_id = message.metadata.get("_reaction_message_id")
                if emoji and react_msg_id:
                    emoji_name = _emoji_to_slack_name(emoji)
                    if not emoji_name:
                        return None
                    try:
                        await self._web_client.reactions_add(
                            channel=channel_id,
                            name=emoji_name,
                            timestamp=str(react_msg_id),
                        )
                        logger.debug(f"Added reaction {emoji_name} to message {react_msg_id}")
                    except Exception as e:
                        err = str(e)
                        if "already_reacted" not in err:
                            logger.warning(
                                f"Failed to add reaction {emoji_name} to message {react_msg_id}: {e}"
                            )
                return None

            # Streaming: skip intermediate chunks, send only the final text
            phase = message.stream_phase
            if phase in (StreamPhase.START, StreamPhase.CHUNK):
                return None  # wait for END to send the full response
            if phase == StreamPhase.DONE:
                return None  # already sent on END

            content = text_from_parts(message.parts) or ""

            if not content:
                return None

            # Convert markdown to Slack mrkdwn
            content = _markdown_to_slack_mrkdwn(content)

            # Determine if replying to a message
            reply_to_message = self._cfg("reply_to_message", True)
            thread_ts = None
            if reply_to_message and message.metadata:
                thread_ts = message.metadata.get("message_id")

            sent_id = None
            for chunk in _split_message(content):
                response = await self._web_client.chat_postMessage(
                    channel=channel_id,
                    text=chunk,
                    thread_ts=thread_ts,
                )
                sent_id = response.get("ts")
                logger.debug(f"Slack message sent: {sent_id}")

            return sent_id

        except Exception as e:
            logger.error(f"Slack send error: {e}")
            return None
