from __future__ import annotations

import re
from pathlib import Path

import aiohttp

# Maps emoji characters → Slack reaction names.
# Covers the 74 Telegram-allowed emojis (which is the realistic set agents will use)
# plus a few extras. Slack uses names without colons, e.g. "thumbsup" not ":thumbsup:".
_EMOJI_TO_SLACK: dict[str, str] = {
    '👍': 'thumbsup',
    '👎': 'thumbsdown',
    '❤': 'heart',
    '❤️': 'heart',
    '🔥': 'fire',
    '🥰': 'smiling_face_with_3_hearts',
    '👏': 'clap',
    '😁': 'grin',
    '🤔': 'thinking_face',
    '🤯': 'exploding_head',
    '😱': 'scream',
    '🤬': 'face_with_symbols_on_mouth',
    '😢': 'cry',
    '🎉': 'tada',
    '🤩': 'star-struck',
    '🤮': 'face_vomiting',
    '💩': 'hankey',
    '🙏': 'pray',
    '👌': 'ok_hand',
    '🕊': 'dove_of_peace',
    '🤡': 'clown_face',
    '🥱': 'yawning_face',
    '🥴': 'woozy_face',
    '😍': 'heart_eyes',
    '🐳': 'whale',
    '❤️‍🔥': 'heart_on_fire',
    '🌚': 'new_moon_with_face',
    '🌭': 'hotdog',
    '💯': '100',
    '🤣': 'rofl',
    '⚡': 'zap',
    '🍌': 'banana',
    '🏆': 'trophy',
    '💔': 'broken_heart',
    '🤨': 'raised_eyebrow',
    '😐': 'neutral_face',
    '🍓': 'strawberry',
    '🍾': 'champagne',
    '💋': 'kiss',
    '🖕': 'middle_finger',
    '😈': 'smiling_imp',
    '😴': 'sleeping',
    '😭': 'sob',
    '🤓': 'nerd_face',
    '👻': 'ghost',
    '👨‍💻': 'technologist',
    '👀': 'eyes',
    '🎃': 'jack_o_lantern',
    '🙈': 'see_no_evil',
    '😇': 'innocent',
    '😨': 'fearful',
    '🤝': 'handshake',
    '✍': 'writing_hand',
    '✍️': 'writing_hand',
    '🤗': 'hugging_face',
    '🫡': 'saluting_face',
    '🎅': 'santa',
    '🎄': 'christmas_tree',
    '☃': 'snowman',
    '☃️': 'snowman',
    '💅': 'nail_care',
    '🤪': 'zany_face',
    '🗿': 'moyai',
    '🆒': 'cool',
    '💘': 'cupid',
    '🙉': 'hear_no_evil',
    '🦄': 'unicorn_face',
    '😘': 'kissing_heart',
    '💊': 'pill',
    '🙊': 'speak_no_evil',
    '😎': 'sunglasses',
    '👾': 'space_invader',
    '🤷‍♂️': 'man-shrugging',
    '🤷': 'shrug',
    '🤷‍♀️': 'woman-shrugging',
    '😡': 'rage',
    '✅': 'white_check_mark',
    '❌': 'x',
    '⭐': 'star',
    '🚀': 'rocket',
    '💡': 'bulb',
    '🎯': 'dart',
    '🔔': 'bell',
    '📌': 'pushpin',
}


def emoji_to_slack_name(emoji: str) -> str:
    """Convert an emoji character to a Slack reaction name.

    Falls back to the input unchanged if no mapping exists
    (callers may already be passing a Slack name like 'thumbsup').
    """
    return _EMOJI_TO_SLACK.get(emoji, emoji)

_MEDIA_DIR = Path.home() / '.operator' / 'media'
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


_SLACK_MSG_LIMIT = 4000


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format."""
    import re
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

    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"`{code}`")

    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"```\n{code}\n```")

    return text


def split_message(text: str, limit: int = _SLACK_MSG_LIMIT) -> list[str]:
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
