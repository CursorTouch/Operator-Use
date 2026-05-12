from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional, TYPE_CHECKING
from program.llm.api.types import APIResponse
import asyncio

if TYPE_CHECKING:
    from program.message.types import TextContent, ThinkingContent, ToolCallContent


class TransportType(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    WEBSOCKET = "websocket"
    SSE = "sse"


class AuthType(str, Enum):
    ApiKey = "api_key"
    OAuth = "oauth"


class StopReason(str, Enum):
    Stop = "stop"
    Length = "length"
    ToolCalls = "tool_calls"
    ContentFilter = "content_filter"
    Abort = "abort"
    Error = "error"

# Needs to bind this to the api layer (Thinking Effort->Thinking Budgets for the Providers don't support Thinking Effort directly which uses the Thinking Budget instead)
class ThinkingBudgets:
    minimal:Optional[int]
    low:Optional[int]
    medium:Optional[int]
    high:Optional[int]
    xhigh:Optional[int]
    max:Optional[int]


class ThinkingLevel(str, Enum):
    Low = "low"
    Minimal = "minimal"
    Medium = "medium"
    High = "high"
    XHigh = "xhigh"
    Max = "max"


class LLMEventType(str, Enum):
    Start = "start"
    Error = "error"
    End = "end"
    TextStart = "text_start"
    TextDelta = "text_delta"
    TextEnd = "text_end"
    ThinkingStart = "thinking_start"
    ThinkingDelta = "thinking_delta"
    ThinkingEnd = "thinking_end"
    ToolCallStart = "tool_call_start"
    ToolCallDelta = "tool_call_delta"
    ToolCallEnd = "tool_call_end"


AbortSignal = asyncio.Event
PayloadCallback = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
ResponseCallback = Callable[[APIResponse], None]


@dataclass
class Options:
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    max_retries: int = 3
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=10))
    temperature: float = 1.0
    max_tokens: Optional[int] = None
    thinking_level: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
    signal: Optional[AbortSignal] = None
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


def _default_text_event_data():
    from program.message.types import TextContent
    return TextContent(content="")


@dataclass
class TextEventData:
    text: "TextContent" = field(default_factory=_default_text_event_data)


@dataclass
class ThinkingEventData:
    thinking: Optional["ThinkingContent"] = None


@dataclass
class ToolCallEventData:
    tool_call: Optional["ToolCallContent"] = None


@dataclass
class StartEvent:
    type: LLMEventType = field(default=LLMEventType.Start, init=False)


@dataclass
class ErrorEvent:
    type: LLMEventType = field(default=LLMEventType.Error, init=False)
    reason: StopReason = StopReason.Stop
    error: str = ""


@dataclass
class EndEvent:
    type: LLMEventType = field(default=LLMEventType.End, init=False)
    reason: StopReason = StopReason.Stop


@dataclass
class TextStartEvent:
    type: LLMEventType = field(default=LLMEventType.TextStart, init=False)
    text: "TextContent"


@dataclass
class TextDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.TextDelta, init=False)
    text: "TextContent"


@dataclass
class TextEndEvent:
    type: LLMEventType = field(default=LLMEventType.TextEnd, init=False)
    text: "TextContent"


@dataclass
class ThinkingStartEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingStart, init=False)
    thinking: "ThinkingContent"


@dataclass
class ThinkingDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingDelta, init=False)
    thinking: "ThinkingContent"


@dataclass
class ThinkingEndEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingEnd, init=False)
    thinking: "ThinkingContent"


@dataclass
class ToolCallStartEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallStart, init=False)
    tool_call: "ToolCallContent"


@dataclass
class ToolCallDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallDelta, init=False)
    tool_call: "ToolCallContent"


@dataclass
class ToolCallEndEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallEnd, init=False)
    tool_call: "ToolCallContent"


LLMEvent = (
    StartEvent
    | ErrorEvent
    | EndEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
)
