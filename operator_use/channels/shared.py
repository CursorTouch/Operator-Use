from __future__ import annotations

import re

THINKING_MAX_CHARS = 800

COMMAND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def build_retry_label(text: str, attempt: int, total: int, is_final: bool) -> str:
    """Format a user-visible retry/failure status label for display in channels."""
    if is_final:
        return f"❌ {text}" if total <= 1 else f"❌ {text}\n(failed after {total} attempt{'s' if total != 1 else ''})"
    return f"❌ {text}\n⏳ Retrying… ({attempt}/{total})"


def format_thinking_label(buffered: str) -> str:
    """Truncate and prefix thinking text for the rolling thinking-status message."""
    display = buffered[:THINKING_MAX_CHARS]
    if len(buffered) > THINKING_MAX_CHARS:
        display += "…"
    return f"💭 {display}"
