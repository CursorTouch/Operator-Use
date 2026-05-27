from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TYPE_CHECKING, Any, Optional, Annotated
from enum import Enum
from PIL import Image
from operator_use.inference.types import StopReason
from operator_use.tool.types import ToolKind
from operator_use.message.utils import image_to_base64, audio_to_base64

if TYPE_CHECKING:
    from operator_use.session.types import CustomMessageEntry, BranchEntry, CompactionEntry


@dataclass
class TextContent:
    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class ImageContent:
    type: Literal["image"] = "image"
    images: list[str | Image.Image | bytes] = field(default_factory=list)

    def to_base64(self) -> list[tuple[str, str]]:
        """Returns (base64_data, mime_type) pairs. URL strings are passed through as-is."""
        return [image_to_base64(img) for img in self.images]

    @classmethod
    def from_file(cls, path: str | Path) -> ImageContent:
        return cls(images=[Path(path).read_bytes()])

    @classmethod
    def from_url(cls, url: str) -> ImageContent:
        return cls(images=[url])


@dataclass
class AudioContent:
    type: Literal["audio"] = "audio"
    # Each item is raw bytes, a base64 string, or a file path string prefixed with "file:".
    audio: list[bytes | str] = field(default_factory=list)

    def to_base64(self) -> list[tuple[str, str]]:
        """Returns (base64_data, mime_type) pairs for each audio item."""
        return [audio_to_base64(item) for item in self.audio]

    @classmethod
    def from_file(cls, path: str | Path) -> AudioContent:
        return cls(audio=[Path(path).read_bytes()])

    @classmethod
    def from_base64(cls, data: str, mime_type: str | None = None) -> AudioContent:
        return cls(audio=[data])


@dataclass
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    content: str = ""
    signature: str = ""


@dataclass
class ToolCallContent:
    type: Literal["tool_call"] = "tool_call"
    id: str = ""
    name: str = ""
    kind: Optional[ToolKind] = None
    args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultContent:
    type: Literal["tool_result"] = "tool_result"
    id: str = ""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False


Content = TextContent | ImageContent | AudioContent | ThinkingContent | ToolCallContent | ToolResultContent

# Per-role content constraints (for type hints and documentation).
SystemContent = TextContent
UserContent = TextContent | ImageContent | AudioContent | ToolResultContent
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


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    CUSTOM = "custom"
    BRANCH_SUMMARY = "branch_summary"
    COMPACTION_SUMMARY = "compaction_summary"


@dataclass
class BaseMessage:
    role: Role
    contents: list[Content] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


@dataclass
class SystemMessage(BaseMessage):
    role: Literal[Role.SYSTEM] = Role.SYSTEM  # pyright: ignore[reportIncompatibleVariableOverride]

    @classmethod
    def text(cls, content: str) -> SystemMessage:
        return cls(contents=[TextContent(content=content)])


@dataclass
class UserMessage(BaseMessage):
    role: Literal[Role.USER] = Role.USER  # pyright: ignore[reportIncompatibleVariableOverride]

    @classmethod
    def text(cls, content: str) -> UserMessage:
        return cls(contents=[TextContent(content=content)])

    @classmethod
    def with_images(cls, content: str, images: list[str | Image.Image | bytes]) -> UserMessage:
        return cls(contents=[TextContent(content=content), ImageContent(images=images)])

    @classmethod
    def with_audio(cls, content: str, audio: list[bytes | str]) -> UserMessage:
        return cls(contents=[TextContent(content=content), AudioContent(audio=audio)])


@dataclass
class AssistantMessage(BaseMessage):
    role: Literal[Role.ASSISTANT] = Role.ASSISTANT  # pyright: ignore[reportIncompatibleVariableOverride]
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
    role: Literal[Role.TOOL] = Role.TOOL  # pyright: ignore[reportIncompatibleVariableOverride]

    @classmethod
    def from_results(cls, results: list[ToolResultContent]) -> ToolMessage:
        return cls(contents=list(results))  # type: ignore[arg-type]

    @classmethod
    def from_result(cls, result: ToolResultContent) -> ToolMessage:
        return cls(contents=[result])  # type: ignore[arg-type]


LLMMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass
class CustomMessage:
    role: Literal[Role.CUSTOM] = field(default=Role.CUSTOM, init=False)
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
    role: Literal[Role.BRANCH_SUMMARY] = field(default=Role.BRANCH_SUMMARY, init=False)
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
    role: Literal[Role.COMPACTION_SUMMARY] = field(default=Role.COMPACTION_SUMMARY, init=False)
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


SessionMessage = CustomMessage | BranchSummaryMessage | CompactionSummaryMessage

AgentMessage = LLMMessage | SessionMessage