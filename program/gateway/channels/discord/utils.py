from __future__ import annotations

from pathlib import Path

_DISCORD_MSG_LIMIT = 2000
_MEDIA_DIR = Path.home() / '.program' / 'media'

_AUDIO_EXTENSIONS = {'.mp3', '.ogg', '.wav', '.m4a', '.aac', '.flac', '.opus', '.webm'}


def is_audio_attachment(attachment) -> bool:
    """Return True if the Discord attachment is an audio file."""
    ct = getattr(attachment, 'content_type', '') or ''
    name = attachment.filename.lower()
    return 'audio' in ct or any(name.endswith(e) for e in _AUDIO_EXTENSIONS)
