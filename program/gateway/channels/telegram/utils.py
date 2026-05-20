from __future__ import annotations

import re
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


def markdown_to_telegram_html(text: str) -> str:
    """Convert standard Markdown to Telegram-safe HTML."""
    if not text:
        return ""

    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # Strip headers and blockquotes to plain text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # Escape HTML entities before inserting tags
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def split_message(text: str, limit: int = _TELEGRAM_MSG_LIMIT) -> list[str]:
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
