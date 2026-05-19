from __future__ import annotations

import re
from pathlib import Path

import aiohttp

_MEDIA_DIR = Path.home() / '.program' / 'media'
_MENTION_RE = re.compile(r'<@[A-Z0-9]+>')

_AUDIO_MIMES = {'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/wav', 'audio/aac', 'audio/flac'}
_AUDIO_EXTENSIONS = {'.ogg', '.mp3', '.mp4', '.m4a', '.webm', '.wav', '.aac', '.flac'}


def is_audio_file(file: dict) -> bool:
    """Return True if the Slack file dict represents an audio file."""
    mime = file.get('mimetype', '')
    name = file.get('name', '').lower()
    return mime in _AUDIO_MIMES or any(name.endswith(e) for e in _AUDIO_EXTENSIONS)


def audio_ext_from_file(file: dict) -> str:
    """Derive file extension from a Slack file dict."""
    name = file.get('name', '')
    if name and '.' in name:
        return Path(name).suffix.lower()
    ext_map = {
        'audio/ogg': '.ogg', 'audio/mpeg': '.mp3', 'audio/mp4': '.m4a',
        'audio/webm': '.webm', 'audio/wav': '.wav', 'audio/aac': '.aac',
    }
    return ext_map.get(file.get('mimetype', ''), '.mp3')


async def download_slack_file(url: str, bot_token: str, dest: Path) -> bool:
    """Download a Slack private file using bearer auth. Returns True on success."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={'Authorization': f'Bearer {bot_token}'}) as resp:
                if resp.status != 200:
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(await resp.read())
        return True
    except Exception:
        return False
