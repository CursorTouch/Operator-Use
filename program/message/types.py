from __future__ import annotations
import base64
import io
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING, Any, Optional
from enum import Enum
from PIL import Image

from program.llm.types import StopReason
from program.tool.types import ToolKind


@dataclass
class TextContent:
    type: Literal["text"] = field(default="text", init=False)
    content: str = ""


@dataclass
class ImageContent:
    type: Literal["image"] = field(default="image", init=False)
    images: list[str | Image.Image | bytes] = field(default_factory=list)

    def to_base64(self) -> list[str]:
        result = []
        for img in self.images:
            if isinstance(img, str):
                result.append(img)
            elif isinstance(img, Image.Image):
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                result.append(base64.b64encode(buf.getvalue()).decode())
            else:
                result.append(base64.b64encode(img).decode())
        return result


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


Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage
