from __future__ import annotations
import base64
import io
from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING
from PIL import Image
from program.llm.types import StopReason

if TYPE_CHECKING:
    pass


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
    args: dict = field(default_factory=dict)


Content = TextContent | ImageContent | ThinkingContent | ToolCallContent


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


@dataclass
class BaseMessage:
    role: str
    contents: list[Content] = field(default_factory=list)

@dataclass
class SystemMessage(BaseMessage):
    role: Literal["system"] = field(default="system", init=False)

@dataclass
class UserMessage(BaseMessage):
    role: Literal["user"] = field(default="user", init=False)


@dataclass
class AssistantMessage(BaseMessage):
    role: Literal["assistant"] = field(default="assistant", init=False)
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.Stop


@dataclass
class ToolMessage(BaseMessage):
    role: Literal["tool"] = field(default="tool", init=False)
    id: str = ""
