from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional
import asyncio


class AuthType(str, Enum):
    ApiKey = "api_key"
    OAuth = "oauth"


class StopReason(str, Enum):
    Stop = "stop"
    Length = "length"
    ToolCalls = "tool_calls"
    ContentFilter = "content_filter"
    Abort = "abort"


class ThinkingLevel(str, Enum):
    Low = "low"
    Minimal = "minimal"
    Medium = "medium"
    High = "high"
    XHigh = "xhigh"
    Max = "max"


AbortSignal = asyncio.Event
PayloadCallback = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
ResponseCallback = Callable[[int, dict[str, str]], None]


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


@dataclass
class TextEventData:
    index: int = 0
    text: str = ""


@dataclass
class ThinkingEventData:
    index: int = 0
    thinking: str = ""


@dataclass
class ToolCallEventData:
    index: int = 0
    id: str = ""
    name: str = ""
    args: str = ""


@dataclass
class LLMEvent:
    type: str


@dataclass
class StartEvent(LLMEvent):
    type: str = field(default="start", init=False)


@dataclass
class ErrorEvent(LLMEvent):
    type: str = field(default="error", init=False)
    reason: StopReason = StopReason.Stop
    message: str = ""


@dataclass
class DoneEvent(LLMEvent):
    type: str = field(default="done", init=False)
    reason: StopReason = StopReason.Stop


@dataclass
class TextStartEvent(LLMEvent):
    type: str = field(default="text_start", init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class TextDeltaEvent(LLMEvent):
    type: str = field(default="text_delta", init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class TextEndEvent(LLMEvent):
    type: str = field(default="text_end", init=False)
    data: TextEventData = field(default_factory=TextEventData)


@dataclass
class ThinkingStartEvent(LLMEvent):
    type: str = field(default="thinking_start", init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ThinkingDeltaEvent(LLMEvent):
    type: str = field(default="thinking_delta", init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ThinkingEndEvent(LLMEvent):
    type: str = field(default="thinking_end", init=False)
    data: ThinkingEventData = field(default_factory=ThinkingEventData)


@dataclass
class ToolCallStartEvent(LLMEvent):
    type: str = field(default="tool_call_start", init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)


@dataclass
class ToolCallDeltaEvent(LLMEvent):
    type: str = field(default="tool_call_delta", init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)


@dataclass
class ToolCallEndEvent(LLMEvent):
    type: str = field(default="tool_call_end", init=False)
    data: ToolCallEventData = field(default_factory=ToolCallEventData)
