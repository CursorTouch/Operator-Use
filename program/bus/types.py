from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Sequence


class StreamPhase(str, Enum):
    START = "start"
    CHUNK = "chunk"
    END = "end"
    DONE = "done"
    ERROR = "error"


@dataclass
class TextPart:
    content: str

@dataclass
class ImagePart:
    images: list[str] = field(default_factory=list)
    paths: list[str] | None = None
    mime_type: str | None = None

@dataclass
class AudioPart:
    audio: str  # file path or transcribed text
    mime_type: str | None = None

@dataclass
class FilePart:
    path: str
    mime_type: str | None = None

ContentPart = TextPart | ImagePart | AudioPart | FilePart


def text_from_parts(parts: Sequence[ContentPart]) -> str:
    return "\n".join(p.content for p in parts if isinstance(p, TextPart))

def media_paths_from_parts(parts: Sequence[ContentPart]) -> list[str]:
    result = []
    for p in parts:
        if isinstance(p, AudioPart): result.append(p.audio)
        elif isinstance(p, FilePart): result.append(p.path)
        elif isinstance(p, ImagePart) and p.paths: result.extend(p.paths)
    return result


@dataclass
class IncomingMessage:
    channel: str      # "telegram", "discord", "slack", "ws:connid", "stdio"
    chat_id: str      # unique conversation within the channel
    parts: list[ContentPart] = field(default_factory=list)
    user_id: str = ""
    message_id: str = ""   # channel-side message ID (used for reactions, threading)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class OutgoingMessage:
    channel: str
    chat_id: str
    parts: list[ContentPart] = field(default_factory=list)
    stream_phase: StreamPhase | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
