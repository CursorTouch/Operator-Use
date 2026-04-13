"""Bus message models: BaseMessage, IncomingMessage, OutgoingMessage."""

from datetime import datetime
from enum import Enum
from typing import Any
import asyncio

from pydantic import BaseModel, ConfigDict, Field


class TextPart(BaseModel):
    """Text content."""

    content: str


class ImagePart(BaseModel):
    """Image(s) as base64 or file paths. Base64: images=[b64]. Paths: paths=[path, ...]."""

    images: list[str] = Field(default_factory=list)
    paths: list[str] | None = None  # File paths (outgoing); images= base64 (incoming)
    mime_type: str | None = None  # image/jpeg, image/png, etc.


class AudioPart(BaseModel):
    """Audio: transcribed text or file path."""

    audio: str


class FilePart(BaseModel):
    """File by path."""

    path: str


# Union of all part types for BaseMessage.parts
ContentPart = TextPart | ImagePart | AudioPart | FilePart


def text_from_parts(parts: list[ContentPart]) -> str:
    """Join text from TextPart only. AudioPart/ImagePart/FilePart excluded (handled as media)."""
    if not parts:
        return ""
    texts: list[str] = []
    for p in parts:
        if isinstance(p, TextPart):
            texts.append(p.content)
    return "\n".join(texts)


def media_paths_from_parts(parts: list[ContentPart]) -> list[str]:
    """Extract file paths from AudioPart, FilePart, and ImagePart (paths)."""
    result: list[str] = []
    for p in parts:
        if isinstance(p, AudioPart):
            result.append(p.audio)
        elif isinstance(p, FilePart):
            result.append(p.path)
        elif isinstance(p, ImagePart) and p.paths:
            result.extend(p.paths)
    return result


class StreamPhase(str, Enum):
    """Streaming phases for OutgoingMessage."""

    START = "start"
    CHUNK = "chunk"
    END = "end"
    DONE = "done"


class BaseMessage(BaseModel):
    """Base message with common fields for bus routing."""

    channel: str
    chat_id: str
    parts: list[ContentPart] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class IncomingMessage(BaseMessage):
    """Message coming into the system (user, heartbeat, whatsapp, telegram, etc.)."""

    user_id: str = ""
    account_id: str = ""  # Which bot account received this (for per-agent channel routing)


class OutgoingMessage(BaseMessage):
    """Message going out from the agent to channels (whatsapp, telegram, display, etc.)."""

    account_id: str = ""  # Which bot account should send this (for per-agent channel routing)
    reply: bool = False
    stream_phase: StreamPhase | None = (
        None  # Streaming: start, chunk, end, done. None = normal message.
    )
    continue_typing: bool = (
        False  # If True, restart typing indicator after sending (for intermediate messages).
    )
    # Optional future: channel resolves this with the sent message_id after delivery.
    # Lets the agent learn the channel-assigned ID without touching the bus.
    sent_id_future: asyncio.Future[int | None] | None = None
