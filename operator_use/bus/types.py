from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Sequence


class StreamPhase(str, Enum):
    """Message stream state transitions during channel transmission."""
    START = "start"
    CHUNK = "chunk"
    END = "end"
    DONE = "done"
    ERROR = "error"


@dataclass
class TextPart:
    """Textual content part of a message."""
    content: str

@dataclass
class ImagePart:
    """Image content part (URLs or file paths)."""
    images: list[str] = field(default_factory=list)
    paths: list[str] | None = None
    mime_type: str | None = None

@dataclass
class AudioPart:
    """Audio content part (file path or transcribed text)."""
    audio: str  # file path or transcribed text
    mime_type: str | None = None

@dataclass
class FilePart:
    """File attachment content part."""
    path: str
    mime_type: str | None = None

ContentPart = TextPart | ImagePart | AudioPart | FilePart


def text_from_parts(parts: Sequence[ContentPart]) -> str:
    """Extract all TextPart contents, joined by newlines."""
    return "\n".join(p.content for p in parts if isinstance(p, TextPart))

def media_paths_from_parts(parts: Sequence[ContentPart]) -> list[str]:
    """Extract audio/file/image paths from content parts."""
    result = []
    for p in parts:
        if isinstance(p, AudioPart): result.append(p.audio)
        elif isinstance(p, FilePart): result.append(p.path)
        elif isinstance(p, ImagePart) and p.paths: result.extend(p.paths)
    return result


@dataclass
class IncomingMessage:
    """Message received from a gateway channel to the agent."""
    channel: str      # "telegram", "discord", "slack", "ws:connid", "stdio"
    chat_id: str      # unique conversation within the channel
    parts: list[ContentPart] = field(default_factory=list)
    user_id: str = ""
    message_id: str = ""   # channel-side message ID (used for reactions, threading)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class OutgoingMessage:
    """Message sent from the agent to a gateway channel."""
    channel: str
    chat_id: str
    parts: list[ContentPart] = field(default_factory=list)
    stream_phase: StreamPhase | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
