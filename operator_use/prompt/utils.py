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
        "\n\nOperator documentation (read only when asked about Operator internals, "
        "architecture, or how to extend it):\n"
        f"- Documentation: {docs_path}\n"
        "- When asked about: turn flow/retry/compaction (agent.md), "
        "LLM loop/tool execution (engine.md), "
        "session JSONL/branching (session.md), "
        "extension loading (extensions.md), "
        "package install (packages.md), "
        "event hooks (hooks.md), "
        "channels/message bus (gateway.md), "
        "models/providers (inference.md), "
        "tool interface (tool.md), "
        "skills format (skill.md), "
        "slash commands (commands.md), "
        "credentials/OAuth (auth.md), "
        "agent profiles (profiles.md), "
        "browser automation (browser.md), "
        "desktop control (computer.md), "
        "sandbox policy (sandbox.md), "
        "knowledge base (knowledge.md), "
        "multi-agent teams (team.md), "
        "Python workflows (workflow.md), "
        "ACP transports (acp.md)\n"
        "- When working on Operator topics, read the relevant doc(s) and follow "
        "cross-references before implementing\n"
        "- Always read .md files completely and follow links to related docs"
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
        '# Skills — FIRST PRIORITY (MANDATORY)',
        '',
        'CRITICAL: You have installed skills that provide specialized capabilities.',
        'Before attempting ANY task — simple or complex — you MUST check if an installed skill handles it.',
        '',
        '## Rules (MUST follow in order)',
        '1. ALWAYS scan the skill list below BEFORE taking ANY action on a user request',
        '2. If a skill\'s description matches or partially matches the task, you MUST load its full',
        '   instructions using the `skill` tool: `skill action="view" name="<name>"` — do this BEFORE anything else',
        '3. Follow the loaded skill instructions EXACTLY — do NOT improvise or use alternative approaches',
        '4. NEVER use general-purpose workarounds when a skill provides the right tool',
        '5. If multiple skills could apply, load the most specific one first',
        '6. Even for seemingly simple tasks, CHECK SKILLS FIRST — skills often handle edge cases',
        '   and produce higher quality results than ad-hoc approaches',
        '',
        '## Enforcement',
        '- If you skip checking skills and use a raw approach for a task that a skill handles,',
        '  this is considered a FAILURE. Always check skills first.',
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
