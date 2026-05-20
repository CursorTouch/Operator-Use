from __future__ import annotations

from pathlib import Path

_DISCORD_MSG_LIMIT = 2000
_MEDIA_DIR = Path.home() / '.program' / 'media'

_AUDIO_EXTENSIONS = {'.mp3', '.ogg', '.wav', '.m4a', '.aac', '.flac', '.opus', '.webm'}


def split_message(text: str, limit: int = _DISCORD_MSG_LIMIT) -> list[str]:
    """Split text into chunks within limit, preferring line breaks."""
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text[:limit]
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = limit
        chunks.append(text[:pos])
        text = text[pos:].lstrip()
    return chunks


def is_audio_attachment(attachment) -> bool:
    """Return True if the Discord attachment is an audio file."""
    ct = getattr(attachment, 'content_type', '') or ''
    name = attachment.filename.lower()
    return 'audio' in ct or any(name.endswith(e) for e in _AUDIO_EXTENSIONS)
