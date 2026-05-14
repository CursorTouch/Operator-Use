"""Shared utilities for compaction and session summarization."""

from __future__ import annotations
from typing import List, Dict, Optional, TYPE_CHECKING
from program.compaction.types import FileOperations, ContextUsageEstimate, CompactionSettings
from program.session.types import SessionEntry, LLMMessageEntry
from program.tool.types import ToolKind

if TYPE_CHECKING:
    from program.message.types import LLMMessage, AssistantMessage, Usage


def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations(read=set(), written=set(), edited=set())


def extract_file_ops_from_message(
    message: LLMMessage,
    file_ops: FileOperations,
) -> None:
    """Extract file operations from an LLM message's tool calls.

    Args:
        message: LLM message potentially containing tool calls
        file_ops: FileOperations object to accumulate results into
    """
    from program.message.types import AssistantMessage, ToolCallContent

    if not isinstance(message, AssistantMessage):
        return

    for content in message.contents:
        if not isinstance(content,ToolCallContent):
            continue

        args = content.args or {}
        tool_kind = content.kind

        match tool_kind:
            case ToolKind.Read if isinstance(args, dict):
                path = args.get("path") or args.get("file_path")
                if path and isinstance(path, str):
                    file_ops.read.add(path)

            case ToolKind.Write if isinstance(args, dict):
                path = args.get("path") or args.get("file_path")
                if path and isinstance(path, str):
                    file_ops.written.add(path)
            case ToolKind.Edit if isinstance(args, dict):
                path = args.get("path") or args.get("file_path")
                if path and isinstance(path, str):
                    file_ops.edited.add(path)

            case ToolKind.Shell if isinstance(args, dict):
                command = args.get("cmd") or args.get("command")
                if command and isinstance(command, str):
                    _extract_bash_file_ops(command, file_ops)

            case _:
                pass


def compute_file_lists(file_ops: FileOperations) -> Dict[str, List[str]]:
    """Compute final file lists from file operations.

    Returns:
        Dict with "read_files" (only read, not modified) and "modified_files"
    """
    modified = set(file_ops.edited) | set(file_ops.written)
    read_only = sorted([f for f in file_ops.read if f not in modified])
    modified_files = sorted(list(modified))

    return {
        "read_files": read_only,
        "modified_files": modified_files,
    }


def format_file_operations(read_files: List[str], modified_files: List[str]) -> str:
    """Format file operations as XML tags for summary.

    Args:
        read_files: List of files that were read
        modified_files: List of files that were modified

    Returns:
        Formatted string with XML tags, or empty string if no files
    """
    sections: List[str] = []

    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")

    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")

    if not sections:
        return ""

    return f"\n\n{chr(10).join(sections)}"


def truncate_for_summary(text: str, max_chars: int = 2000) -> str:
    """Truncate text for summarization.

    Keeps the beginning and appends a truncation marker.

    Args:
        text: Text to potentially truncate
        max_chars: Maximum characters to keep

    Returns:
        Original text if <= max_chars, otherwise truncated with marker
    """
    if len(text) <= max_chars:
        return text

    truncated_chars = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def get_message_from_entry(entry: SessionEntry) -> Optional[LLMMessage]:
    """Extract LLM message from any session entry.

    Args:
        entry: Session entry to extract message from

    Returns:
        LLMMessage if entry is LLMMessageEntry, None otherwise
    """
    if isinstance(entry, LLMMessageEntry):
        return entry.message
    return None


def get_message_from_entry_for_compaction(entry: SessionEntry) -> Optional[LLMMessage]:
    """Extract LLM message from entry specifically for compaction.

    This is the same as get_message_from_entry but provided for semantic clarity
    when extracting messages for summarization purposes.

    Args:
        entry: Session entry to extract message from

    Returns:
        LLMMessage if entry is LLMMessageEntry, None otherwise
    """
    return get_message_from_entry(entry)


def _get_assistant_usage(message: LLMMessage) -> Optional[Usage]:
    """Return usage from an assistant message, skipping aborted/errored ones."""
    from program.llm.types import StopReason
    from program.message.types import AssistantMessage as _AssistantMessage
    if not isinstance(message, _AssistantMessage):
        return None
    if message.stop_reason in (StopReason.Abort, StopReason.Error):
        return None
    usage = message.usage
    if not usage or usage.input_tokens == 0:
        return None
    return usage


def calculate_context_tokens(usage: Usage) -> int:
    """Sum all token components from a Usage object."""
    return usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens


def get_last_assistant_usage(entries: List[SessionEntry]) -> Optional[Usage]:
    """Find usage from the last non-aborted assistant message in session entries."""
    for entry in reversed(entries):
        if isinstance(entry, LLMMessageEntry):
            usage = _get_assistant_usage(entry.message)
            if usage:
                return usage
    return None


def should_compact(
    current_tokens: int,
    context_window: int,
    settings: CompactionSettings,
) -> bool:
    """Determine if compaction should be performed.

    Args:
        current_tokens: Current context token count
        context_window: Model's total context window size
        settings: Compaction settings (must have .enabled and .reserve_tokens)

    Returns:
        True if compaction is needed, False otherwise
    """
    if not settings.enabled:
        return False
    return current_tokens > context_window - settings.reserve_tokens


def serialize_conversation(messages: List[LLMMessage]) -> str:
    """Serialize LLM messages to plain text for summarization prompts."""
    from program.message.types import TextContent, ThinkingContent, ToolCallContent, ToolResultContent, Role
    import json

    parts: List[str] = []
    for msg in messages:
        role = getattr(msg, "role", None)
        if role is None:
            continue
        role_label = role.value.upper() if hasattr(role, "value") else str(role).upper()
        contents = getattr(msg, "contents", [])
        lines: List[str] = []
        for block in contents:
            if isinstance(block, TextContent) and block.content:
                lines.append(block.content)
            elif isinstance(block, ThinkingContent) and block.content:
                lines.append(f"[thinking] {block.content} [/thinking]")
            elif isinstance(block, ToolCallContent):
                lines.append(f"[tool_call] {block.name} {json.dumps(block.args)} [/tool_call]")
            elif isinstance(block, ToolResultContent):
                lines.append(f"[tool_result] {block.id} {block.content} [/tool_result]")
        if lines:
            parts.append(f"{role_label}: {' '.join(lines)}")
    return "\n\n".join(parts)


def _extract_bash_file_ops(command: str, file_ops: FileOperations) -> None:
    """Extract file operations from a bash command string.

    Args:
        command: Bash command to analyze
        file_ops: FileOperations object to accumulate results into
    """
    import re

    # Simple heuristic: look for common file operation patterns
    # Read patterns: cat, grep, find, ls, head, tail, etc.
    read_patterns = [
        r"cat\s+(['\"]?)([^'\";\s]+)\1",
        r"grep\s+.*\s+(['\"]?)([^'\";\s]+)\1",
        r"find\s+(['\"]?)([^'\";\s]+)\1",
        r"head\s+.*\s+(['\"]?)([^'\";\s]+)\1",
        r"tail\s+.*\s+(['\"]?)([^'\";\s]+)\1",
    ]

    for pattern in read_patterns:
        matches = re.finditer(pattern, command)
        for match in matches:
            file_path = match.group(2) if match.lastindex >= 2 else match.group(1)
            if file_path and not file_path.startswith("-"):
                file_ops.read.add(file_path)

    # Write patterns: >, >>, tee, cp, mv, etc.
    if ">>" in command or ">" in command:
        write_pattern = r"(?:>>?|tee)\s+(['\"]?)([^'\";\s]+)\1"
        matches = re.finditer(write_pattern, command)
        for match in matches:
            file_path = match.group(2) if match.lastindex >= 2 else match.group(1)
            if file_path and not file_path.startswith("-"):
                file_ops.written.add(file_path)
