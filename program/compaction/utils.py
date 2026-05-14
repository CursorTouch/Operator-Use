"""Shared utilities for compaction and session summarization."""

from __future__ import annotations
from typing import Set, List, Dict, Any, Optional, TYPE_CHECKING
from program.compaction.types import FileOperations, ContextUsageEstimate
from program.session.types import SessionEntry, LLMMessageEntry
from program.tool.types import ToolKind

if TYPE_CHECKING:
    from program.message.types import LLMMessage, AssistantMessage


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


def get_assistant_usage(message: LLMMessage) -> ContextUsageEstimate:
    """Extract usage information from an assistant message.

    Args:
        message: LLM message (typically an AssistantMessage)

    Returns:
        ContextUsageEstimate with token counts from the message
    """
    if not hasattr(message, "usage"):
        return ContextUsageEstimate()

    usage = message.usage
    total = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )

    return ContextUsageEstimate(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        total_tokens=total,
    )


def get_last_assistant_usage(messages: List[LLMMessage]) -> ContextUsageEstimate:
    """Get usage information from the last assistant message in a list.

    Args:
        messages: List of LLM messages

    Returns:
        ContextUsageEstimate from the last assistant message, or empty if none found
    """
    for message in reversed(messages):
        if hasattr(message, "role"):
            from program.message.types import Role

            if message.role == Role.ASSISTANT:
                return get_assistant_usage(message)

    return ContextUsageEstimate()


def get_last_assistant_usage_info(messages: List[LLMMessage]) -> str:
    """Format last assistant message's usage as a readable string.

    Args:
        messages: List of LLM messages

    Returns:
        Formatted usage string, or empty string if no assistant message found
    """
    usage = get_last_assistant_usage(messages)

    if usage.total_tokens == 0:
        return ""

    parts = [f"Total: {usage.total_tokens}"]
    if usage.input_tokens:
        parts.append(f"In: {usage.input_tokens}")
    if usage.output_tokens:
        parts.append(f"Out: {usage.output_tokens}")
    if usage.cache_read_tokens:
        parts.append(f"Cache: {usage.cache_read_tokens}")
    if usage.cache_write_tokens:
        parts.append(f"CacheW: {usage.cache_write_tokens}")

    return f"[{', '.join(parts)}]"


def calculate_context_tokens(messages: List[LLMMessage]) -> int:
    """Calculate actual context tokens from message usage data.

    Sums the input tokens from all assistant messages, providing actual
    token counts from the LLM rather than estimates.

    Args:
        messages: List of LLM messages

    Returns:
        Total input tokens from all assistant messages
    """
    total = 0
    for message in messages:
        if hasattr(message, "usage"):
            total += message.usage.input_tokens
    return total


def should_compact(
    current_tokens: int,
    reserve_tokens: int = 16384,
) -> bool:
    """Determine if compaction should be performed.

    Args:
        current_tokens: Current context token count
        reserve_tokens: Token reserve threshold (default 16384)

    Returns:
        True if compaction is needed, False otherwise
    """
    return current_tokens > reserve_tokens


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
