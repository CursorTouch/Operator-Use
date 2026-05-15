from __future__ import annotations

import json
from typing import Any

from program.llm.types import StopReason
from program.message.types import (
    AgentMessage, AssistantMessage, UserMessage, ToolMessage,
    CustomMessage, BranchSummaryMessage, CompactionSummaryMessage,
    TextContent, ThinkingContent, ImageContent, ToolCallContent, ToolResultContent,
    Usage, Role,
)
from program.session.types import (
    SessionEntry, MessageEntry, CompactionEntry, BranchEntry, CustomMessageEntry,
)
from program.tool.types import ToolKind
from program.compaction.types import (
    FileOperations, CutPointResult, ContextUsageEstimate,
)


# ============================================================================
# Token calculation
# ============================================================================

def calculate_context_tokens(usage: Usage) -> int:
    return usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens


def estimate_tokens(message: AgentMessage) -> int:
    chars = 0
    match message:
        case UserMessage(contents=contents):
            for content in contents:
                match content:
                    case TextContent(content=text):
                        chars += len(text)
                    case ImageContent():
                        chars += 4800
        case AssistantMessage(contents=contents):
            for content in contents:
                match content:
                    case TextContent(content=text):
                        chars += len(text)
                    case ThinkingContent(content=text):
                        chars += len(text)
                    case ToolCallContent(name=name, args=args):
                        chars += len(name) + len(json.dumps(args))
        case ToolMessage(contents=contents):
            for content in contents:
                match content:
                    case ToolResultContent(content=text):
                        chars += len(text)
        case BranchSummaryMessage(summary=summary) | CompactionSummaryMessage(summary=summary):
            chars = len(summary)
        case CustomMessage(contents=contents):
            for content in contents:
                match content:
                    case TextContent(content=text):
                        chars += len(text)
    return max(1, chars // 4)


def get_assistant_usage(message:AgentMessage) -> Usage|None:
    if isinstance(message, AssistantMessage):
        if message.stop_reason not in (StopReason.Error, StopReason.Abort) and message.usage:
            return message.usage
    return None


def get_last_assistant_usage(entries: list[SessionEntry]) -> Usage | None:
    for entry in reversed(entries):
        if message := get_message_from_entry(entry=entry):
            if usage := get_assistant_usage(message=message):
                return usage
    return None

def get_last_assistant_usage_info(messages: list[AgentMessage])->tuple[Usage, int] | None:
    for i, message in enumerate(reversed(messages)):
        if usage := get_assistant_usage(message=message):
            original_index = len(messages) - 1 - i
            return (usage, original_index)
    return None


def estimate_context_tokens(messages: list[AgentMessage]) -> ContextUsageEstimate:
    usage_info = get_last_assistant_usage_info(messages=messages)
    if usage_info is None:
        total = sum(estimate_tokens(m) for m in messages)
        return ContextUsageEstimate(
            tokens=total,
            usage_tokens=0,
            trailing_tokens=total,
            last_usage_index=None,
        )

    usage, last_usage_index = usage_info
    usage_tokens = calculate_context_tokens(usage=usage)
    trailing = sum(estimate_tokens(messages[i]) for i in range(last_usage_index + 1, len(messages)))
    return ContextUsageEstimate(
        tokens=usage_tokens + trailing,
        usage_tokens=usage_tokens,
        trailing_tokens=trailing,
        last_usage_index=last_usage_index,
    )


# ============================================================================
# File operation tracking
# ============================================================================

def extract_file_ops_from_message(msg: AgentMessage, file_ops: FileOperations) -> None:
    match msg:
        case AssistantMessage(contents=contents):
            for content in contents:
                match content:
                    case ToolCallContent(kind=kind, args=args):
                        path = args.get("path") if args else None
                        if not path or not isinstance(path, str):
                            continue
                        match kind:
                            case ToolKind.Read:
                                file_ops.read.add(path)
                            case ToolKind.Write:
                                file_ops.written.add(path)
                            case ToolKind.Edit:
                                file_ops.edited.add(path)


def compute_file_lists(file_ops: FileOperations) -> tuple[list[str], list[str]]:
    modified = file_ops.edited | file_ops.written
    read_files = sorted(file_ops.read - modified)
    modified_files = sorted(modified)
    return read_files, modified_files


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    sections: list[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


# ============================================================================
# Message extraction from session entries
# ============================================================================

def get_message_from_entry(entry: SessionEntry) -> AgentMessage | None:
    match entry:
        case MessageEntry(message=message):
            return message
        case CustomMessageEntry():
            return CustomMessage.from_session(entry=entry)
        case BranchEntry():
            return BranchSummaryMessage.from_session(entry=entry)
        case CompactionEntry():
            return CompactionSummaryMessage.from_session(entry=entry)
        case _:
            return None


def get_message_from_entry_for_compaction(entry: SessionEntry) -> AgentMessage | None:
    if isinstance(entry, CompactionEntry):
        return None
    return get_message_from_entry(entry)


# ============================================================================
# Cut point detection
# ============================================================================

def find_valid_cut_points(
    entries: list[SessionEntry], start_index: int, end_index: int
) -> list[int]:
    cut_points: list[int] = []
    for i in range(start_index, end_index):
        entry = entries[i]
        match entry:
            case MessageEntry():
                match entry.message.role:
                    case Role.USER | Role.ASSISTANT | Role.CUSTOM | Role.BRANCH_SUMMARY | Role.COMPACTION_SUMMARY:
                        cut_points.append(i)
            case BranchEntry() | CustomMessageEntry():
                cut_points.append(i)
    return cut_points


def find_turn_start_index(
    entries: list[SessionEntry], entry_index: int, start_index: int
) -> int:
    for i in range(entry_index, start_index - 1, -1):
        entry = entries[i]
        match entry:
            case BranchEntry() | CustomMessageEntry():
                return i
            case MessageEntry(message=message):
                match message.role:
                    case Role.USER:
                        return i
                    case _:  # Role.ASSISTANT | Role.CUSTOM | Role.BRANCH_SUMMARY | Role.COMPACTION_SUMMARY
                        continue
            case _: # Label
                continue
    return -1


def find_cut_point(
    entries: list[SessionEntry],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
) -> CutPointResult:
    cut_points = find_valid_cut_points(entries, start_index, end_index)

    if not cut_points:
        return CutPointResult(
            first_kept_entry_index=start_index,
            turn_start_index=-1,
            is_split_turn=False,
        )

    accumulated = 0
    cut_index = cut_points[0]

    for i in range(end_index - 1, start_index - 1, -1):
        entry = entries[i]
        if not isinstance(entry, MessageEntry):
            continue
        accumulated += estimate_tokens(entry.message)
        if accumulated >= keep_recent_tokens:
            for c in cut_points:
                if c >= i:
                    cut_index = c
                    break
            break

    # Pull cut_index back over non-message, non-compaction entries
    while cut_index > start_index:
        prev = entries[cut_index - 1]
        if isinstance(prev, (CompactionEntry, MessageEntry)):
            break
        cut_index -= 1

    cut_entry = entries[cut_index]
    is_user_message = (
        isinstance(cut_entry, MessageEntry) and cut_entry.message.role == Role.USER
    )
    turn_start_index = (
        -1 if is_user_message
        else find_turn_start_index(entries, cut_index, start_index)
    )

    return CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=not is_user_message and turn_start_index != -1,
    )


# ============================================================================
# Prompt builders
# ============================================================================

def build_summary_prompt(
    messages: list,
    previous_summary: str | None,
    custom_instructions: str | None,
    summarization_prompt: str,
    update_summarization_prompt: str,
) -> str:
    base = update_summarization_prompt if previous_summary else summarization_prompt
    if custom_instructions:
        base = f"{base}\n\nAdditional focus: {custom_instructions}"
    conversation_text = serialize_conversation(messages)
    prompt = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if previous_summary:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    return prompt + base


def build_turn_prefix_prompt(messages: list, turn_prefix_prompt: str) -> str:
    conversation_text = serialize_conversation(messages)
    return f"<conversation>\n{conversation_text}\n</conversation>\n\n{turn_prefix_prompt}"


# ============================================================================
# LLM event helpers
# ============================================================================

def extract_text_from_events(events: list, error_label: str) -> str:
    """Collect text from LLM events, raising RuntimeError on error stop reason."""
    from program.llm.types import TextEndEvent, EndEvent, ErrorEvent, StopReason
    text_parts: list[str] = []
    stop_reason = StopReason.Stop
    error = ""
    for event in events:
        match event:
            case TextEndEvent():
                text_parts.append(event.text.content)
            case EndEvent():
                stop_reason = event.reason
            case ErrorEvent():
                stop_reason = event.reason
                error = event.error
    if stop_reason == StopReason.Error:
        raise RuntimeError(f"{error_label}: {error or 'Unknown error'}")
    return "".join(text_parts)


# ============================================================================
# Compaction boundary helpers  (used by Compaction.prepare)
# ============================================================================

def find_prev_compaction_index(entries: list[SessionEntry]) -> int:
    """Return the index of the most recent CompactionEntry, or -1."""
    for i in reversed(range(len(entries))):
        if isinstance(entries[i], CompactionEntry):
            return i
    return -1


def resolve_boundary_start(entries: list[SessionEntry], prev_compaction_index: int) -> int:
    """Return the first entry index to include when summarising after a prior compaction."""
    if prev_compaction_index < 0:
        return 0
    prev = entries[prev_compaction_index]
    assert isinstance(prev, CompactionEntry)
    retained_idx = next(
        (i for i, e in enumerate(entries) if e.id == prev.first_kept_entry_id), -1
    )
    return retained_idx if retained_idx >= 0 else prev_compaction_index + 1


def collect_messages_in_range(
    entries: list[SessionEntry],
    start: int,
    end: int,
    for_compaction: bool = False,
) -> list[AgentMessage]:
    """Extract AgentMessages from entries[start:end]."""
    fn = get_message_from_entry_for_compaction if for_compaction else get_message_from_entry
    return [msg for i in range(start, end) if (msg := fn(entries[i])) is not None]


def build_file_ops_from_prev_compaction(
    entries: list[SessionEntry],
    prev_compaction_index: int,
) -> FileOperations:
    """Seed a FileOperations from the saved details of the previous compaction."""
    from program.compaction.types import CompactionDetails  # local to avoid circular at module level
    file_ops = FileOperations()
    if prev_compaction_index < 0:
        return file_ops
    entry = entries[prev_compaction_index]
    if not isinstance(entry, CompactionEntry):
        return file_ops
    details = entry.details
    if isinstance(details, CompactionDetails):
        for f in details.read_files:
            file_ops.read.add(f)
        for f in details.modified_files:
            file_ops.edited.add(f)
    elif isinstance(details, dict):
        for f in details.get("read_files", []):
            file_ops.read.add(f)
        for f in details.get("modified_files", []):
            file_ops.edited.add(f)
    return file_ops


def accumulate_file_ops(file_ops: FileOperations, messages: list[AgentMessage]) -> None:
    """Add file ops extracted from each message into file_ops in-place."""
    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)


# ============================================================================
# Conversation serialization
# ============================================================================

_TOOL_RESULT_MAX_CHARS = 2000


def _truncate_for_summary(text: str, max_chars: int = _TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    truncated_chars = len(text) - max_chars
    return f"{text[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def serialize_conversation(messages: list[AgentMessage]) -> str:
    parts: list[str] = []
    for msg in messages:
        match msg:
            case UserMessage(contents=contents):
                text_parts: list[str] = []
                for c in contents:
                    match c:
                        case TextContent(content=text):
                            text_parts.append(text)
                if text_parts:
                    parts.append(f"[User]: {'\n'.join(text_parts)}")
            case AssistantMessage(contents=contents):
                thinking_parts: list[str] = []
                text_parts: list[str] = []
                tool_calls: list[str] = []
                for c in contents:
                    match c:
                        case ThinkingContent(content=text):
                            thinking_parts.append(text)
                        case TextContent(content=text):
                            text_parts.append(text)
                        case ToolCallContent(name=name, args=args):
                            tool_calls.append(f"{name}({', '.join(f'{k}={json.dumps(v)}' for k, v in (args or {}).items())})")
                if thinking_parts:
                    parts.append(f"[Assistant thinking]: {'\n'.join(thinking_parts)}")
                if text_parts:
                    parts.append(f"[Assistant]: {'\n'.join(text_parts)}")
                if tool_calls:
                    parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")
            case ToolMessage(contents=contents):
                text_parts: list[str] = []
                for c in contents:
                    match c:
                        case ToolResultContent(content=text):
                            text_parts.append(text)
                if text_parts:
                    parts.append(f"[Tool result]: {_truncate_for_summary('\n'.join(text_parts))}")
            case CustomMessage(custom_type=custom_type, contents=contents):
                text_parts: list[str] = []
                for c in contents:
                    match c:
                        case TextContent(content=text):
                            text_parts.append(text)
                if text_parts:
                    parts.append(f"[{custom_type.upper()}]: {'\n'.join(text_parts)}")
            case BranchSummaryMessage(summary=summary) | CompactionSummaryMessage(summary=summary):
                parts.append(f"[{type(msg).__name__.replace('Message', '')}]: {summary}")
    return "\n\n".join(parts)
