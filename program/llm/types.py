from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional, TYPE_CHECKING
from program.llm.api.types import APIResponse
import asyncio

if TYPE_CHECKING:
    from program.message.types import TextContent, ThinkingContent, ToolCallContent


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
    Done = "done"
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
    thinking_budget: Optional[int] = None
    signal: Optional[AbortSignal] = None
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


def _default_text_event_data():
    from program.message.types import TextContent
    return TextContent(content="")


@dataclass
class TextEventData:
    text: Any = field(default_factory=_default_text_event_data)


@dataclass
class ThinkingEventData:
    thinking: Optional[Any] = None


@dataclass
class ToolCallEventData:
    tool_call: Optional[Any] = None


@dataclass
class StartEvent:
    type: LLMEventType = field(default=LLMEventType.Start, init=False)


@dataclass
class ErrorEvent:
    type: LLMEventType = field(default=LLMEventType.Error, init=False)
    reason: StopReason = StopReason.Stop
    message: str = ""


@dataclass
class EndEvent:
    type: LLMEventType = field(default=LLMEventType.Done, init=False)
    reason: StopReason = StopReason.Stop


@dataclass
class TextStartEvent:
    type: LLMEventType = field(default=LLMEventType.TextStart, init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class TextDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.TextDelta, init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class TextEndEvent:
    type: LLMEventType = field(default=LLMEventType.TextEnd, init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class ThinkingStartEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingStart, init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ThinkingDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingDelta, init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ThinkingEndEvent:
    type: LLMEventType = field(default=LLMEventType.ThinkingEnd, init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ToolCallStartEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallStart, init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)


@dataclass
class ToolCallDeltaEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallDelta, init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)


@dataclass
class ToolCallEndEvent:
    type: LLMEventType = field(default=LLMEventType.ToolCallEnd, init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)


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
