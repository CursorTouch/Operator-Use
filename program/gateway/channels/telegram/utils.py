from __future__ import annotations

from pathlib import Path

_TELEGRAM_MSG_LIMIT = 4096
_MEDIA_DIR = Path.home() / '.program' / 'media'


def audio_mime_ext(mime_type: str | None) -> str:
    """Map audio MIME type to a file extension."""
    if not mime_type:
        return '.ogg'
    m = mime_type.lower()
    if 'ogg' in m:
        return '.ogg'
    if 'mp4' in m or 'm4a' in m:
        return '.m4a'
    if 'aac' in m:
        return '.aac'
    if 'wav' in m:
        return '.wav'
    if 'mp3' in m or 'mpeg' in m:
        return '.mp3'
    return '.ogg'
