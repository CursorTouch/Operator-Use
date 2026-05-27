from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from program.resource.types import ContextFile
    from program.skill.types import Skill


# Per-channel formatting and behaviour hints injected into the system prompt
# so the agent adapts its output to what each platform can actually render.
_CHANNEL_HINTS: dict[str, str] = {
    "stdio": (
        "Platform: terminal / CLI. Full markdown is supported — use headers, "
        "tables, fenced code blocks, and bold/italic freely. No message-length limit."
    ),
    "telegram": (
        "Platform: Telegram. Use MarkdownV2 or plain text only — no HTML. "
        "Avoid tables (they do not render). Max 4096 characters per message; "
        "split long responses into multiple messages if needed. "
        "Inline images and files are sent via the bot API, not markdown syntax."
    ),
    "discord": (
        "Platform: Discord. Standard markdown is supported: **bold**, *italic*, "
        "`inline code`, ```fenced code blocks```. No native table support. "
        "Max 2000 characters per message — split if needed."
    ),
    "slack": (
        "Platform: Slack. Uses mrkdwn, NOT standard markdown. "
        "Bold: *text*, italic: _text_, code: `code`, code block: ```code```. "
        "Avoid standard markdown headers (#) and HTML. Max 40 000 chars per message."
    ),
    "email": (
        "Platform: Email. Use plain prose with clear paragraph breaks. "
        "Avoid markdown syntax — it will appear as raw characters. "
        "Keep responses concise; the user will see this as an email reply."
    ),
    "twitch": (
        "Platform: Twitch chat. Plain text only — no markdown, no formatting. "
        "Max 500 characters per message. Be very concise."
    ),
}


def channel_hint(channel_id: str | None) -> str:
    """Return the platform hint string for the given channel, or '' if unknown."""
    if not channel_id:
        return ""
    # WebSocket sessions use dynamic IDs like "ws:connid" — normalise to "websocket"
    key = "websocket" if channel_id.startswith("ws:") else channel_id.lower()
    hint = _CHANNEL_HINTS.get(key, "")
    if not hint:
        return ""
    return f"\n\n# Platform\n\n{hint}"


def build_guidelines(extra: list[str]) -> str:
    lines = [f"- {g.strip()}" for g in extra if g.strip()]
    return "\n".join(lines)


def context_files_section(context_files: list[ContextFile]) -> str:
    if not context_files:
        return ""
    parts = ["\n\n# Project Context\n\nProject-specific instructions and guidelines:\n"]
    for cf in context_files:
        parts.append(f"## {cf.path}\n\n{cf.content}\n")
    return "\n".join(parts)


def escape_xml(text: str) -> str:
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
    )


def format_skills_for_prompt(skills: list[Skill], available_tools: set[str] | None = None) -> str:
    visible = [
        s for s in skills
        if not s.disable_model_invocation
        and (
            not s.requires_tools
            or available_tools is None
            or all(t in available_tools for t in s.requires_tools)
        )
    ]
    if not visible:
        return ''

    lines = [
        '',
        '',
        'The following skills provide specialized instructions for specific tasks.',
        'Before replying to any task, scan the skills below. If a skill matches or is even '
        'partially relevant, you MUST load it with skill action="view" before proceeding.',
        'When a skill file references a relative path, resolve it against the skill directory '
        '(parent of SKILL.md) and use that absolute path in tool commands.',
        '',
        '<available_skills>',
    ]

    for skill in visible:
        lines.append('  <skill>')
        lines.append(f'    <name>{escape_xml(skill.name)}</name>')
        lines.append(f'    <description>{escape_xml(skill.description)}</description>')
        lines.append('  </skill>')

    lines.append('</available_skills>')
    return '\n'.join(lines)
