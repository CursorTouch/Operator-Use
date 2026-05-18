from __future__ import annotations

from program.acp.types import MessagePart, TextMessagePart


def text_from_parts(parts: list[MessagePart]) -> str:
    """Extract and concatenate all text from a list of message parts."""
    return ''.join(p.text for p in parts if isinstance(p, TextMessagePart))


def parts_from_text(text: str) -> list[MessagePart]:
    """Wrap a plain string into a single TextMessagePart list."""
    return [TextMessagePart(text=text)]
