from __future__ import annotations

import base64
import io
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TYPE_CHECKING, Any, Optional
from enum import Enum
from PIL import Image

from program.llm.types import StopReason
from program.tool.types import ToolKind

if TYPE_CHECKING:
    from program.session.types import CustomMessageEntry, BranchEntry, CompactionEntry


_PIL_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def _detect_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


@dataclass
class TextContent:
    type: Literal["text"] = field(default="text", init=False)
    content: str = ""


@dataclass
class ImageContent:
    type: Literal["image"] = field(default="image", init=False)
    images: list[str | Image.Image | bytes] = field(default_factory=list)

    def to_base64(self) -> list[tuple[str, str]]:
        """Returns (base64_data, mime_type) pairs. URL strings are passed through as-is."""
        result: list[tuple[str, str]] = []
        for img in self.images:
            if isinstance(img, str):
                if img.startswith("http"):
                    result.append((img, ""))
                else:
                    try:
                        mime = _detect_mime(base64.b64decode(img[:16] + "=="))
                    except Exception:
                        mime = "image/png"
                    result.append((img, mime))
            elif isinstance(img, Image.Image):
                fmt = (img.format or "PNG").upper()
                buf = io.BytesIO()
                img.save(buf, format=fmt)
                mime = _PIL_MIME.get(fmt, "image/png")
                result.append((base64.b64encode(buf.getvalue()).decode(), mime))
            else:
                mime = _detect_mime(img)
                result.append((base64.b64encode(img).decode(), mime))
        return result

    @classmethod
    def from_file(cls, path: str | Path) -> ImageContent:
        return cls(images=[Path(path).read_bytes()])

    @classmethod
    def from_url(cls, url: str) -> ImageContent:
        return cls(images=[url])


@dataclass
class ThinkingContent:
    type: Literal["thinking"] = field(default="thinking", init=False)
    content: str = ""
    signature: str = ""


@dataclass
class ToolCallContent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    id: str = ""
    name: str = ""
    kind: Optional[ToolKind] = None
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultContent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    id: str = ""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


Content = TextContent | ImageContent | ThinkingContent | ToolCallContent | ToolResultContent

# Per-role content constraints (for type hints and documentation).
SystemContent = TextContent
UserContent = TextContent | ImageContent | ToolResultContent
AssistantContent = TextContent | ThinkingContent | ToolCallContent
ToolContent = ToolResultContent


@dataclass
class UsageCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: UsageCost = field(default_factory=UsageCost)


class Role(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    CUSTOM = "custom"
    BRANCH_SUMMARY="branch_summary"
    COMPACTION_SUMMARY="compaction_summary"


@dataclass
class BaseMessage:
    role: Role
    contents: list[Content] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class SystemMessage(BaseMessage):
    role: Role = field(default=Role.SYSTEM, init=False)

    @classmethod
    def text(cls, content: str) -> SystemMessage:
        return cls(contents=[TextContent(content=content)])


@dataclass
class UserMessage(BaseMessage):
    role: Role = field(default=Role.USER, init=False)

    @classmethod
    def text(cls, content: str) -> UserMessage:
        return cls(contents=[TextContent(content=content)])

    @classmethod
    def with_images(cls, content: str, images: list[str | Image.Image | bytes]) -> UserMessage:
        return cls(contents=[TextContent(content=content), ImageContent(images=images)])


@dataclass
class AssistantMessage(BaseMessage):
    role: Role = field(default=Role.ASSISTANT, init=False)
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.Stop
    error: str = ""

    def text_content(self) -> str:
        return "".join(c.content for c in self.contents if isinstance(c, TextContent))

    def tool_calls(self) -> list[ToolCallContent]:
        return [c for c in self.contents if isinstance(c, ToolCallContent)]

    def thinking(self) -> list[ThinkingContent]:
        return [c for c in self.contents if isinstance(c, ThinkingContent)]


@dataclass
class ToolMessage(BaseMessage):
    role: Role = field(default=Role.TOOL, init=False)

    @classmethod
    def from_results(cls, results: list[ToolResultContent]) -> ToolMessage:
        return cls(contents=results)  # type: ignore[arg-type]

    @classmethod
    def from_result(cls, result: ToolResultContent) -> ToolMessage:
        return cls(contents=[result])  # type: ignore[arg-type]


LLMMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass
class CustomMessage:
    role: Role = field(default=Role.CUSTOM, init=False)
    custom_type: str
    timestamp: float
    contents: list[TextContent | ImageContent] = field(default_factory=list)
    details: Any | None = None

    @classmethod
    def from_session(cls, entry: CustomMessageEntry) -> CustomMessage:
        raw = entry.content
        if isinstance(raw, list):
            contents = raw
        elif isinstance(raw, str):
            contents = [TextContent(content=raw)]
        else:
            contents = []
        return cls(
            custom_type=entry.custom_type,
            contents=contents,
            timestamp=entry.timestamp,
            details=entry.details
        )


@dataclass
class BranchSummaryMessage:
    role: Role = field(default=Role.BRANCH_SUMMARY, init=False)
    summary: str
    from_id:str
    timestamp:float

    @classmethod
    def from_session(cls,entry:BranchEntry)->BranchSummaryMessage:
        return cls(
            summary=entry.summary,
            from_id=entry.from_id,
            timestamp=entry.timestamp
        )

@dataclass
class CompactionSummaryMessage:
    role: Role = field(default=Role.COMPACTION_SUMMARY, init=False)
    summary: str
    tokens_before:int
    timestamp:float

    @classmethod
    def from_session(cls,entry:CompactionEntry)->CompactionSummaryMessage:
        return cls(
            summary=entry.summary,
            tokens_before=entry.tokens_before,
            timestamp=entry.timestamp
        )


SessionMessage = CustomMessage|BranchSummaryMessage|CompactionSummaryMessage

AgentMessage = LLMMessage|SessionMessage