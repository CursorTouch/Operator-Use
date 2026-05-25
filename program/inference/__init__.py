"""
Top-level access to inference clients and shared types.

Usage:
    from program.inference import LLM, ImageLLM
    from program.inference import LLMContext, LLMOptions, StopReason
    from program.inference import ImageContext, ImageOptions, GeneratedImage
"""

# Shared types — no circular-import risk (inference/types.py has no heavy deps)
from program.inference.types import (
    # LLM
    LLMContext,
    LLMEvent,
    LLMEventType,
    LLMOptions,
    StructuredResponseFormat,
    StructuredResponseInput,
    normalize_structured_response_format,
    StopReason,
    ThinkingLevel,
    ThinkingBudgets,
    Transport,
    # LLM events
    StartEvent,
    EndEvent,
    ErrorEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    # Image
    ImageContext,
    ImageOptions,
    GeneratedImage,
    ImageStopReason,
)


def _get_llm_class():
    from program.inference.api.text.service import LLM
    return LLM


def _get_image_llm_class():
    from program.inference.api.image.service import ImageLLM
    return ImageLLM


class LLM:
    """
    Thin proxy so `from program.inference import LLM` works without triggering
    circular imports at parse time. Instantiation delegates to the real class.
    """
    def __new__(cls, *args, **kwargs):
        real = _get_llm_class()
        return real(*args, **kwargs)

    @classmethod
    def __class_getitem__(cls, item):
        return _get_llm_class().__class_getitem__(item)


class ImageLLM:
    """
    Thin proxy so `from program.inference import ImageLLM` works without
    triggering circular imports at parse time.
    """
    def __new__(cls, *args, **kwargs):
        real = _get_image_llm_class()
        return real(*args, **kwargs)


__all__ = [
    # Clients
    "LLM",
    "ImageLLM",
    # LLM context / options
    "LLMContext",
    "LLMEvent",
    "LLMEventType",
    "LLMOptions",
    "StructuredResponseFormat",
    "StructuredResponseInput",
    "normalize_structured_response_format",
    "StopReason",
    "ThinkingLevel",
    "ThinkingBudgets",
    "Transport",
    # LLM events
    "StartEvent",
    "EndEvent",
    "ErrorEvent",
    "TextStartEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "ThinkingStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ToolCallStartEvent",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    # Image
    "ImageContext",
    "ImageOptions",
    "GeneratedImage",
    "ImageStopReason",
]
