"""Orchestrator: pipeline layer that owns STT/TTS, message building, and agent routing."""

import asyncio
import base64
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from operator_use.bus import Bus, IncomingMessage, OutgoingMessage, StreamPhase
from operator_use.bus.views import (
    AudioPart,
    ContentPart,
    FilePart,
    ImagePart,
    TextPart,
    text_from_parts,
)
from operator_use.messages import AIMessage, HumanMessage, ImageMessage

if TYPE_CHECKING:
    from operator_use.agent.service import Agent
    from operator_use.providers.base import BaseSTT, BaseTTS

logger = logging.getLogger(__name__)


_INLINE_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".jsonl",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".rs",
    ".go",
    ".rb",
    ".sh",
    ".bat",
    ".ps1",
    ".sql",
    ".log",
}

_MAX_INLINE_CHARS = 8_000


def _extract_file_content(path: str) -> str:
    """Inline small text files; for everything else pass the path to the agent."""
    p = Path(path)
    if not p.exists():
        return f"[File not found: {path}]"

    ext = p.suffix.lower()
    name = p.name
    size_kb = p.stat().st_size // 1024

    # Small text / code / data files — inline directly
    if ext in _INLINE_EXTENSIONS:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > _MAX_INLINE_CHARS:
                return (
                    f"[File: {name} ({size_kb} KB) — too large to inline, saved at: {path}]\n"
                    f"Use your file reading tools to access it."
                )
            return f"[File: {name}]\n{text}"
        except Exception as e:
            return f"[File: {name} — read error: {e}, path: {path}]"

    # Binary files (PDF, DOCX, PPTX, XLSX, images, etc.)
    # File is already saved to media folder — tell the agent where it is
    type_label = {
        ".pdf": "PDF",
        ".docx": "Word document",
        ".doc": "Word document",
        ".pptx": "PowerPoint",
        ".ppt": "PowerPoint",
        ".xlsx": "Excel spreadsheet",
        ".xls": "Excel spreadsheet",
        ".zip": "ZIP archive",
        ".mp4": "Video",
        ".mp3": "Audio",
    }.get(ext, "File")

    return (
        f"[{type_label}: {name} ({size_kb} KB) saved at: {path}]\n"
        f"Use your file reading tools to access its contents."
    )


class Orchestrator:
    """Pipeline layer between channels and agents.

    Owns:
    - STT: AudioPart → text before the agent sees the message
    - TTS: AIMessage text → AudioPart before the response is sent
    - Message building: IncomingMessage → HumanMessage/ImageMessage
    - Outgoing building: AIMessage → OutgoingMessage
    - Agent routing: decides which Agent handles each message
    - Bus consumption loop (previously Agent.ainvoke)

    Hooks live exclusively on Agent (tool calls, loop start/end, response modification).
    The Orchestrator has no hooks — extend it via subclassing or the router callable.
    """

    def __init__(
        self,
        bus: Bus,
        agents: "dict[str, Agent]",
        stt: "BaseSTT | None" = None,
        tts: "BaseTTS | None" = None,
        router: "Callable[[IncomingMessage], str] | None" = None,
        default_agent: str = "operator",
        streaming: bool = True,
        gateway=None,
        cron=None,
    ):
        self.bus = bus
        self.agents = agents
        self.stt = stt
        self.tts = tts
        self.router = router or (lambda _: default_agent)
        self.default_agent = default_agent
        self.streaming = streaming
        self.gateway = gateway
        self.cron = cron
        self._running = False

        self._pending_replies: dict[str, asyncio.Future[str]] = {}
        self._session_overrides: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Agent routing
    # ------------------------------------------------------------------

    def _get_session_id(self, message: IncomingMessage) -> str:
        """Return the active session ID for a message, respecting named-session overrides."""
        default = f"{message.channel}:{message.chat_id}"
        return self._session_overrides.get(default, default)

    def _resolve_agent(self, message: IncomingMessage) -> "Agent":
        name = self.router(message)
        agent = self.agents.get(name) or self.agents.get(self.default_agent)
        if agent is None:
            raise ValueError(f"No agent found for name={name!r} and no default agent configured")
        return agent

    # ------------------------------------------------------------------
    # Message building (IncomingMessage → HumanMessage/ImageMessage)
    # ------------------------------------------------------------------

    def _user_sent_voice(self, message: IncomingMessage) -> bool:
        return any(isinstance(p, AudioPart) for p in (message.parts or []))

    async def _build_request_message(
        self, message: IncomingMessage
    ) -> "HumanMessage | ImageMessage":
        """Convert IncomingMessage parts to HumanMessage or ImageMessage.

        Handles STT for AudioPart and base64 decoding for ImagePart.
        """
        from PIL import Image as PILImage

        parts = message.parts or []
        texts: list[str] = []
        images: list = []
        image_paths: list[str] = []

        for part in parts:
            if isinstance(part, TextPart):
                texts.append(part.content)
            elif isinstance(part, AudioPart):
                audio_val = part.audio
                if Path(audio_val).is_file() and self.stt:
                    try:
                        audio_val = await self.stt.atranscribe(audio_val)
                    except Exception as e:
                        logger.warning(f"STT failed for {audio_val}: {e}")
                        audio_val = "[transcription failed]"
                texts.append(audio_val.strip())
            elif isinstance(part, ImagePart) and part.images:
                if part.paths:
                    image_paths.extend(part.paths)
                for b64 in part.images:
                    try:
                        data = base64.b64decode(b64)
                        img = PILImage.open(BytesIO(data)).convert("RGB")
                        images.append(img)
                    except Exception as e:
                        logger.warning(f"Failed to decode image from parts: {e}")
            elif isinstance(part, FilePart):
                texts.append(_extract_file_content(part.path))
            else:
                logger.warning(f"Unsupported part type: {type(part)}")

        content = "\n".join(texts) if texts else ("[image]" if images else "[empty message]")

        if not images:
            return HumanMessage(content=content)

        metadata: dict = {"image_paths": image_paths} if image_paths else {}
        return ImageMessage(
            content=content, images=images, mime_type="image/jpeg", metadata=metadata
        )

    # ------------------------------------------------------------------
    # Outgoing building (AIMessage → OutgoingMessage)
    # ------------------------------------------------------------------

    async def _build_outgoing_message(
        self,
        message: IncomingMessage,
        response: AIMessage,
        streamed: bool,
    ) -> OutgoingMessage:
        """Convert AIMessage to OutgoingMessage.

        Voice-in → voice-out via TTS when configured.
        Text-in → text-out always.
        """
        text = response.content or ""
        clean_text = re.sub(r"^\[(bot_)?msg_id:[^\]]+\]\s*", "", text)
        parts: list[ContentPart] = []

        if self.tts and clean_text and len(clean_text) <= 4000 and self._user_sent_voice(message):
            try:
                fd, path = tempfile.mkstemp(suffix=".wav")
                try:
                    os.close(fd)
                    await self.tts.asynthesize(clean_text, path)
                    parts = [AudioPart(audio=path)]
                except Exception:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                    raise
            except Exception as e:
                logger.warning("TTS failed: %s", e)

        if not parts:
            parts = [TextPart(content=clean_text)]

        return OutgoingMessage(
            chat_id=message.chat_id,
            channel=message.channel,
            account_id=message.account_id,
            parts=parts,
            metadata=message.metadata,
            reply=True,
            stream_phase=StreamPhase.DONE if streamed else None,
        )

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_command(self, message: IncomingMessage) -> None:
        """Handle session/system control commands without running the agent."""
        from operator_use.orchestrator.commands import handle_command

        agent = self._resolve_agent(message)
        await handle_command(message, agent, self.bus, self._session_overrides)

    async def _handle_message(self, request_message: IncomingMessage) -> None:
        """Process one incoming message end-to-end."""
        session_id = self._get_session_id(request_message)

        # Gate: require /start if no session file exists
        agent = self._resolve_agent(request_message)
        if agent.sessions.load(session_id) is None:
            await self.bus.publish_outgoing(
                OutgoingMessage(
                    chat_id=request_message.chat_id,
                    channel=request_message.channel,
                    account_id=request_message.account_id,
                    parts=[TextPart(content="No active session. Type /start to begin.")],
                    metadata=request_message.metadata,
                    reply=True,
                )
            )
            return

        try:
            # Build HumanMessage/ImageMessage (runs STT if needed)
            built_message = await self._build_request_message(request_message)
            if hasattr(built_message, "metadata") and request_message.metadata:
                built_message.metadata = dict(request_message.metadata)

            # Decide streaming: orchestrator knows channel type + voice flag
            use_streaming = (
                self.streaming
                and request_message.channel not in ("direct", "cli")
                and hasattr(agent.llm, "astream")
                and not self._user_sent_voice(request_message)
            )

            streamed = False

            async def publish_stream(content: str, is_final: bool, init: bool = False) -> None:
                nonlocal streamed
                phase = (
                    StreamPhase.START
                    if init
                    else (StreamPhase.END if is_final else StreamPhase.CHUNK)
                )
                if is_final:
                    streamed = True
                await self.bus.publish_outgoing(
                    OutgoingMessage(
                        chat_id=request_message.chat_id,
                        channel=request_message.channel,
                        account_id=request_message.account_id,
                        parts=[TextPart(content=content)],
                        metadata=request_message.metadata,
                        reply=True,
                        stream_phase=phase,
                    )
                )

            # Run the agent loop
            response_message = await agent.run(
                message=built_message,
                session_id=session_id,
                incoming=request_message,
                publish_stream=publish_stream if use_streaming else None,
                pending_replies=self._pending_replies,
            )

            # Build OutgoingMessage (runs TTS if needed)
            outgoing = await self._build_outgoing_message(
                request_message, response_message, streamed
            )

            loop = asyncio.get_event_loop()
            sent_id_future: asyncio.Future[int | None] = loop.create_future()
            outgoing.sent_id_future = sent_id_future
            await self.bus.publish_outgoing(outgoing)

            # Store bot message ID in session for reaction tracking
            try:
                bot_message_id = await asyncio.wait_for(asyncio.shield(sent_id_future), timeout=5.0)
                if bot_message_id is not None:
                    logger.info(f"Resolved bot message ID: {bot_message_id}")
                    session = agent.sessions.get_or_create(session_id=session_id)
                    for msg in reversed(session.messages):
                        if isinstance(msg, AIMessage):
                            if not isinstance(msg.metadata, dict):
                                msg.metadata = {}
                            if msg.metadata.get("message_id") is None:
                                msg.metadata["message_id"] = bot_message_id
                            break
                    agent.sessions.save(session)
            except asyncio.TimeoutError:
                logger.debug("No sent_message_id received within timeout — skipping.")

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            try:
                await self.bus.publish_outgoing(
                    OutgoingMessage(
                        chat_id=request_message.chat_id,
                        channel=request_message.channel,
                        account_id=request_message.account_id,
                        parts=[
                            TextPart(
                                content=f"Sorry, I encountered an error: {type(e).__name__}: {str(e)[:300]}"
                            )
                        ],
                        metadata=request_message.metadata,
                        reply=True,
                    )
                )
            except Exception as send_err:
                logger.error(f"Failed to send error response: {send_err}")

    async def process_direct(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "direct",
        publish_stream: "Callable[..., Awaitable[None]] | None" = None,
    ) -> str:
        """Process a message directly without going through the bus (heartbeat, REPL)."""
        message = IncomingMessage(
            parts=[TextPart(content=content)],
            user_id="user",
            channel=channel,
            chat_id=chat_id,
        )
        agent = self._resolve_agent(message)
        session_id = f"{channel}:{chat_id}"
        built_message = await self._build_request_message(message)
        response = await agent.run(
            message=built_message,
            session_id=session_id,
            incoming=message,
            publish_stream=publish_stream,
            pending_replies=self._pending_replies,
        )
        return response.content or ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Stop the orchestrator and release background resources."""
        self._running = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def ainvoke(self) -> None:
        """Main consume loop — replaces Agent.ainvoke."""
        self._running = True
        logger.info("Orchestrator ainvoke loop started")
        while self._running:
            try:
                request_message = await asyncio.wait_for(self.bus.consume_incoming(), timeout=1.0)
                session_id = self._get_session_id(request_message)
                logger.info(
                    f"Message received | channel={request_message.channel} chat={request_message.chat_id}"
                )

                # Reaction events: update AIMessage metadata, no LLM call
                if request_message.metadata and request_message.metadata.get("_reaction_event"):
                    agent = self._resolve_agent(request_message)
                    await agent._handle_reaction(request_message)
                    continue

                # Session control commands: handle without running the agent
                if request_message.metadata and request_message.metadata.get("_command") in (
                    "start",
                    "stop",
                    "restart",
                ):
                    await self._handle_command(request_message)
                    continue

                # Pending reply: a tool is waiting for the user's next message
                if session_id in self._pending_replies:
                    future = self._pending_replies.pop(session_id)
                    if not future.done():
                        future.set_result(text_from_parts(request_message.parts) or "")
                    continue

                asyncio.create_task(self._handle_message(request_message))

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Unexpected error in ainvoke loop: {e}", exc_info=True)
        self._running = False
