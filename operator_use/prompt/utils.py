from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.skill.types import Skill


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



def docs_section(docs_path: str) -> str:
    return (
        "\n\nOperator's own internals are documented as markdown files in "
        f"{docs_path} (one per subsystem, e.g. agent.md, engine.md, session.md, "
        "gateway.md, inference.md, profiles.md). Only relevant when asked how "
        "Operator works or how to extend it — in that case read the matching "
        "doc(s) in full and follow their cross-references before implementing. "
        "Otherwise ignore this directory."
    )


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
        '# Skills',
        '',
        'Installed skills provide tested, higher-quality procedures for specific tasks.',
        'Before starting a task, scan the list below. If a skill matches, load it with',
        '`skill action="view" name="<name>"` and follow it rather than improvising. Prefer',
        'the most specific match when several apply.',
        '',
        '<available_skills>',
    ]

    for skill in visible:
        location = escape_xml(str(skill.file_path))
        lines.append('  <skill>')
        lines.append(f'    <name>{escape_xml(skill.name)}</name>')
        lines.append(f'    <description>{escape_xml(skill.description)}</description>')
        lines.append(f'    <location>{location}</location>')
        lines.append('  </skill>')

    lines.append('</available_skills>')
    lines.append('')
    lines.append('To load a skill\'s full instructions: `skill action="view" name="<name>"`')
    lines.append('Scripts within a skill are relative to the skill\'s directory (parent of SKILL.md).')
    return '\n'.join(lines)
