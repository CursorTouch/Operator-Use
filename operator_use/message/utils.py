from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


_PIL_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

_AUDIO_MIME: dict[bytes, str] = {
    b"ID3": "audio/mpeg",
    b"\xff\xfb": "audio/mpeg",
    b"\xff\xf3": "audio/mpeg",
    b"\xff\xf2": "audio/mpeg",
    b"OggS": "audio/ogg",
    b"fLaC": "audio/flac",
    b"RIFF": "audio/wav",
}


def detect_image_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def detect_audio_mime(data: bytes) -> str:
    for magic, mime in _AUDIO_MIME.items():
        if data[:len(magic)] == magic:
            if magic == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
                return "audio/wav"
            elif magic != b"RIFF":
                return mime
    return "audio/mpeg"


def image_to_base64(img: str | Image.Image | bytes) -> tuple[str, str]:
    """Return (base64_data, mime_type). URL strings are passed through with empty mime."""
    if isinstance(img, str):
        if img.startswith("http"):
            return img, ""
        try:
            mime = detect_image_mime(base64.b64decode(img[:16] + "=="))
        except Exception:
            mime = "image/png"
        return img, mime
    if isinstance(img, Image.Image):
        fmt = (img.format or "PNG").upper()
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        mime = _PIL_MIME.get(fmt, "image/png")
        return base64.b64encode(buf.getvalue()).decode(), mime
    mime = detect_image_mime(img)
    return base64.b64encode(img).decode(), mime


def audio_to_base64(item: bytes | str) -> tuple[str, str]:
    """Return (base64_data, mime_type). Accepts bytes, base64 string, or 'file:' path."""
    if isinstance(item, bytes):
        mime = detect_audio_mime(item)
        return base64.b64encode(item).decode(), mime
    if item.startswith("file:"):
        data = Path(item[5:]).read_bytes()
        mime = detect_audio_mime(data)
        return base64.b64encode(data).decode(), mime
    try:
        mime = detect_audio_mime(base64.b64decode(item[:16] + "=="))
    except Exception:
        mime = "audio/mpeg"
    return item, mime


def filter_empty_assistant_messages(messages: list) -> list:
    """Remove assistant messages with no usable content from anywhere in history.

    An assistant message with empty contents (e.g. a persisted API error turn)
    produces {"role": "assistant"} with neither content nor tool_calls, which
    all providers reject with a 400. Filter them out before building LLM context.
    """
    from operator_use.message.types import Role, TextContent, ToolCallContent, ThinkingContent
    result = []
    for msg in messages:
        if getattr(msg, 'role', None) == Role.ASSISTANT:
            contents = getattr(msg, 'contents', [])
            has_usable = any(
                isinstance(c, (TextContent, ToolCallContent, ThinkingContent))
                for c in contents
            )
            if not has_usable:
                continue
        result.append(msg)
    return result


def strip_unusable_trailing_assistant(messages: list) -> list:
    """Return messages with unusable trailing assistant turns removed.

    Non-destructive: operates on the given list only (the caller's session
    record should stay append-only). Drops, from the end:

    - an assistant message whose stop_reason is not Stop (error/abort turns), and
    - an assistant message with no usable content (empty turn), and
    - an assistant message containing tool_calls (a trailing assistant is by
      definition unanswered — no tool result follows it),

    because providers reject dangling tool_calls, empty assistant turns, and
    partial error turns. A trailing assistant message with real text and a
    successful stop reason is a legitimately completed turn and is kept.
    """
    from operator_use.message.types import Role, TextContent, ToolCallContent
    from operator_use.inference.types import StopReason

    msgs = list(messages)
    while msgs:
        last = msgs[-1]
        if getattr(last, "role", None) != Role.ASSISTANT:
            break
        stop_reason = getattr(last, "stop_reason", StopReason.Stop)
        if stop_reason != StopReason.Stop:
            msgs.pop()
            continue
        contents = getattr(last, "contents", [])
        has_text = any(
            isinstance(c, TextContent) and c.content.strip() for c in contents
        )
        has_tool_calls = any(isinstance(c, ToolCallContent) for c in contents)
        if has_tool_calls or not has_text:
            msgs.pop()
            continue
        break
    return msgs
