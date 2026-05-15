from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional

from program.message.types import ImageContent, TextContent, Usage


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
    on_payload: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None
    on_response: Optional[Callable[[Any], None]] = None


@dataclass
class ImageContext:
    input: list[TextContent | ImageContent]


@dataclass
class GeneratedImage:
    model_id: str
    provider: str
    output: list[TextContent | ImageContent]
    stop_reason: ImageStopReason
    usage: Usage = field(default_factory=Usage)
    error: str = ""
    timestamp: float = field(default_factory=time.time)
