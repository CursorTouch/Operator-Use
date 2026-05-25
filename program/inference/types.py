from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional, TYPE_CHECKING
import asyncio
import time
from copy import deepcopy

from pydantic import BaseModel

if TYPE_CHECKING:
    from program.message.types import BaseMessage, TextContent, ThinkingContent, ToolCallContent, ImageContent
    from program.tool.types import Tool


# ── Shared enums ──────────────────────────────────────────────────────────────

class Transport(str, Enum):
    Auto = "auto"
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


class ThinkingLevel(str, Enum):
    Off = "off"
    Minimal = "minimal"
    Low = "low"
    Medium = "medium"
    High = "high"
    XHigh = "xhigh"
    Max = "max"


@dataclass
class ThinkingBudgets:
    """Token budgets for providers that map ThinkingLevel to budget_tokens."""
    minimal: Optional[int] = 1024
    low: Optional[int] = 2048
    medium: Optional[int] = 4096
    high: Optional[int] = 8192
    xhigh: Optional[int] = 16384
    max: Optional[int] = 32768

    def get(self, level: ThinkingLevel) -> int:
        _defaults = {
            "minimal": 1024, "low": 2048, "medium": 4096,
            "high": 8192, "xhigh": 16384, "max": 32768,
        }
        value = getattr(self, level.value, None)
        return value if value is not None else _defaults[level.value]


# ── LLM types ─────────────────────────────────────────────────────────────────

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
ResponseCallback = Callable[[Any], None]


@dataclass
class LLMOptions:
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    max_retries: int = 3
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=60))
    temperature: float = 1.0
    max_tokens: Optional[int] = None
    transport: Transport = Transport.HTTP
    thinking_level: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
    signal: Optional[AbortSignal] = None
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


@dataclass
class StructuredResponseFormat:
    schema: dict[str, Any]
    name: str = "response"
    strict: bool = True


StructuredResponseInput = StructuredResponseFormat | type[Any] | dict[str, Any]


def normalize_structured_response_format(response_format: StructuredResponseInput | None) -> StructuredResponseFormat | None:
    if response_format is None:
        return None

    if isinstance(response_format, StructuredResponseFormat):
        return response_format

    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        return StructuredResponseFormat(
            name=response_format.__name__,
            schema=response_format.model_json_schema(),
        )

    if isinstance(response_format, dict):
        schema = deepcopy(response_format)
        if isinstance(schema.get("format"), dict):
            schema = deepcopy(schema["format"])
        if isinstance(schema.get("json_schema"), dict):
            schema = deepcopy(schema["json_schema"])
        name = str(schema.get("name") or schema.get("title") or "response")
        strict = bool(schema.pop("strict", True))
        if "schema" in schema and isinstance(schema["schema"], dict):
            name = str(schema.pop("name", name))
            schema = deepcopy(schema["schema"])
        return StructuredResponseFormat(name=name, schema=schema, strict=strict)

    raise TypeError("response_format must be a Pydantic model class, JSON schema dict, or StructuredResponseFormat")


@dataclass
class LLMContext:
    messages: list["BaseMessage"]
    tools: list["Tool"] = field(default_factory=list)
    system_prompt: Optional[str] = None
    response_format: Optional[StructuredResponseInput] = None


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
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


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
    thinking: Optional["ThinkingContent"] = None


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


# ── Image types ───────────────────────────────────────────────────────────────

class ImageStopReason(str, Enum):
    Stop = "stop"
    Error = "error"
    Abort = "abort"


@dataclass
class ImageOptions:
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=120))
    max_retries: int = 3
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


@dataclass
class ImageContext:
    contents: list["TextContent | ImageContent"]
    size: Optional[str] = None
    quality: Optional[str] = None
    n: int = 1


@dataclass
class GeneratedImage:
    model_id: str
    provider: str
    output: list["TextContent | ImageContent"]
    stop_reason: ImageStopReason
    usage: Any = field(default_factory=lambda: __import__("program.message.types", fromlist=["Usage"]).Usage())
    error: str = ""
    timestamp: float = field(default_factory=time.time)


# ── Video types ───────────────────────────────────────────────────────────────

class VideoFormat(str, Enum):
    MP4  = "mp4"
    WEBM = "webm"
    MOV  = "mov"
    GIF  = "gif"


class VideoStopReason(str, Enum):
    Stop    = "stop"
    Error   = "error"
    Abort   = "abort"
    Timeout = "timeout"


@dataclass
class VideoOptions:
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=600))
    poll_interval: float = 3.0
    max_retries: int = 3
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


@dataclass
class VideoContext:
    prompt: str
    image: Optional[bytes] = None
    duration: Optional[float] = None
    aspect_ratio: Optional[str] = None
    resolution: Optional[str] = None


@dataclass
class GeneratedVideo:
    model_id: str
    provider: str
    url: Optional[str] = None
    video: Optional[bytes] = None
    format: VideoFormat = VideoFormat.MP4
    duration: Optional[float] = None
    stop_reason: VideoStopReason = VideoStopReason.Stop
    usage: Any = None
    error: str = ""
    timestamp: float = field(default_factory=time.time)


# ── Audio types ───────────────────────────────────────────────────────────────

class AudioFormat(str, Enum):
    MP3 = "mp3"
    WAV = "wav"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    PCM = "pcm"


class AudioStopReason(str, Enum):
    Stop = "stop"
    Error = "error"
    Abort = "abort"


class TimestampGranularity(str, Enum):
    Word = "word"
    Segment = "segment"


@dataclass
class AudioOptions:
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=120))
    max_retries: int = 3
    on_payload: Optional[PayloadCallback] = None
    on_response: Optional[ResponseCallback] = None


@dataclass
class TTSContext:
    input: str
    voice: str
    speed: float = 1.0
    response_format: AudioFormat = AudioFormat.MP3
    language: Optional[str] = None
    instructions: Optional[str] = None


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float


@dataclass
class SegmentTimestamp:
    id: int
    text: str
    start: float
    end: float


@dataclass
class STTContext:
    audio: bytes
    format: AudioFormat = AudioFormat.MP3
    language: Optional[str] = None
    temperature: float = 0.0
    timestamp_granularities: list[TimestampGranularity] = field(default_factory=list)
    prompt: Optional[str] = None


@dataclass
class SynthesizedAudio:
    model_id: str
    provider: str
    audio: bytes
    format: AudioFormat
    stop_reason: AudioStopReason
    usage: Any = None
    error: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class TranscribedAudio:
    model_id: str
    provider: str
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    words: list[WordTimestamp] = field(default_factory=list)
    segments: list[SegmentTimestamp] = field(default_factory=list)
    stop_reason: AudioStopReason = AudioStopReason.Stop
    usage: Any = None
    error: str = ""
    timestamp: float = field(default_factory=time.time)
