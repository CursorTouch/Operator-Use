from __future__ import annotations

_TWITCH_MSG_LIMIT = 500


def split_message(text: str, limit: int = _TWITCH_MSG_LIMIT) -> list[str]:
    """Split text into chunks within limit, breaking on newlines then spaces."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text[:limit]
        pos = cut.rfind('\n')
        if pos <= 0:
            pos = cut.rfind(' ')
        if pos <= 0:
            pos = limit
        chunks.append(text[:pos])
        text = text[pos:].lstrip()
    return chunks
