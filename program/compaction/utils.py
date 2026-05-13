"""Shared utilities for compaction and session summarization."""

from __future__ import annotations
from typing import Set, List, Dict, Any, Optional, TYPE_CHECKING
from program.compaction.types import FileOperations

if TYPE_CHECKING:
    from program.message.types import LLMMessage


def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations(read=set(), written=set(), edited=set())


def extract_file_ops_from_message(message: Any, file_ops: FileOperations) -> None:
    """Extract file operations from an LLM message's tool calls.

    Args:
        message: LLM message potentially containing tool calls
        file_ops: FileOperations object to accumulate results into
    """
    # Check if message is an assistant message with content
    if not hasattr(message, "role") or message.role != "assistant":
        return

    if not hasattr(message, "contents"):
        return

    # Process contents for tool calls
    for content_block in message.contents:
        if not hasattr(content_block, "type"):
            continue

        content_type = content_block.type

        # Handle different content types (tool calls would be in thinking or tool metadata)
        # This is a placeholder for tool call extraction - actual implementation
        # depends on message structure


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
