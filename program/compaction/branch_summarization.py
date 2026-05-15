from __future__ import annotations

from typing import TYPE_CHECKING, Any

from program.llm.types import LLMContext, TextEndEvent, EndEvent, ErrorEvent, StopReason, ThinkingLevel
from program.message.types import (
    AgentMessage, UserMessage, TextContent,
    ToolMessage, CustomMessage, BranchSummaryMessage, CompactionSummaryMessage,
)
from program.session.types import (
    SessionEntry, MessageEntry, CustomMessageEntry, BranchEntry, CompactionEntry,
)
from program.compaction.prompts import (
    SUMMARIZATION_SYSTEM_PROMPT,
    BRANCH_SUMMARY_PREAMBLE,
    BRANCH_SUMMARY_PROMPT,
)
from program.compaction.types import (
    FileOperations, BranchPreparation, BranchSummaryDetails,
    BranchSummaryResult, CollectEntriesResult, GenerateBranchSummaryOptions,
)
from program.compaction.utils import (
    estimate_tokens, extract_file_ops_from_message,
    compute_file_lists, format_file_operations, serialize_conversation,
)

if TYPE_CHECKING:
    from program.session.manager import SessionManager

# ============================================================================
# Entry collection
# ============================================================================

def collect_entries_for_branch_summary(
    session: SessionManager,
    old_leaf_id: str | None,
    target_id: str,
) -> CollectEntriesResult:
    """
    Collect entries that should be summarized when navigating from one branch position to another.

    Walks from old_leaf_id back to the common ancestor with target_id, collecting entries
    along the way. Does NOT stop at compaction boundaries.
    """
    if not old_leaf_id:
        return CollectEntriesResult(entries=[], common_ancestor_id=None)

    old_path_ids = {e.id for e in session.get_branch(old_leaf_id)}
    target_path = session.get_branch(target_id)

    # target_path is root-first; iterate backwards to find the deepest common ancestor
    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry.id in old_path_ids:
            common_ancestor_id = entry.id
            break

    # Walk from old leaf back to common ancestor, collecting entries
    entries: list[SessionEntry] = []
    cursor: str | None = old_leaf_id

    while cursor and cursor != common_ancestor_id:
        entry = session.get_entry(cursor)
        if not entry:
            break
        entries.append(entry)
        cursor = entry.parent_id

    entries.reverse()  # chronological order
    return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)

# ============================================================================
# Entry to message conversion
# ============================================================================

def _get_message_from_entry(entry: SessionEntry) -> AgentMessage | None:
    if isinstance(entry, MessageEntry):
        if isinstance(entry.message, ToolMessage):
            return None  # skip tool results; context is in assistant's tool call
        return entry.message
    if isinstance(entry, CustomMessageEntry):
        return CustomMessage.from_session(entry)
    if isinstance(entry, BranchEntry):
        return BranchSummaryMessage.from_session(entry)
    if isinstance(entry, CompactionEntry):
        return CompactionSummaryMessage.from_session(entry)
    return None

# ============================================================================
# Preparation
# ============================================================================

def prepare_branch_entries(
    entries: list[SessionEntry],
    token_budget: int = 0,
) -> BranchPreparation:
    """
    Prepare entries for summarization within a token budget.

    Walks entries from newest to oldest, adding messages until the budget is reached.
    Also collects file operations from all branch summary details (cumulative tracking)
    and from assistant tool calls in included messages.
    """
    messages: list[AgentMessage] = []
    file_ops = FileOperations()
    total_tokens = 0

    # First pass: collect file ops from all branch_summary entries (even outside budget)
    # Only from pi-generated summaries (from_hook != True)
    for entry in entries:
        if isinstance(entry, BranchEntry) and not entry.from_hook and entry.details:
            details = entry.details
            if isinstance(details, dict):
                for f in details.get("read_files", []):
                    file_ops.read.add(f)
                for f in details.get("modified_files", []):
                    file_ops.edited.add(f)
            elif isinstance(details, BranchSummaryDetails):
                for f in details.read_files:
                    file_ops.read.add(f)
                for f in details.modified_files:
                    file_ops.edited.add(f)

    # Second pass: walk newest to oldest, add messages up to token budget
    for entry in reversed(entries):
        message = _get_message_from_entry(entry)
        if not message:
            continue

        extract_file_ops_from_message(message, file_ops)
        tokens = estimate_tokens(message)

        if token_budget > 0 and total_tokens + tokens > token_budget:
            # Summary entries are high-value — try to squeeze them in anyway
            is_summary = isinstance(entry, (CompactionEntry, BranchEntry))
            if is_summary and total_tokens < token_budget * 0.9:
                messages.insert(0, message)
                total_tokens += tokens
            break

        messages.insert(0, message)
        total_tokens += tokens

    return BranchPreparation(messages=messages, file_ops=file_ops, total_tokens=total_tokens)

# ============================================================================
# Summary generation
# ============================================================================

async def generate_branch_summary(
    entries: list[SessionEntry],
    options: GenerateBranchSummaryOptions,
) -> BranchSummaryResult:
    """Generate a summary of the abandoned branch entries."""
    token_budget = options.context_window - options.reserve_tokens
    preparation = prepare_branch_entries(entries, token_budget)

    if not preparation.messages:
        return BranchSummaryResult(summary="No content to summarize")

    # Serialize conversation so the model analyses it rather than continues it
    conversation_text = serialize_conversation(preparation.messages)

    if options.replace_instructions and options.custom_instructions:
        instructions = options.custom_instructions
    elif options.custom_instructions:
        instructions = f"{BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {options.custom_instructions}"
    else:
        instructions = BRANCH_SUMMARY_PROMPT

    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{instructions}"

    llm = options.llm
    context = LLMContext(
        messages=[UserMessage(contents=[TextContent(content=prompt_text)])],
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
    )
    events = await llm.invoke(context)

    text_parts: list[str] = []
    stop_reason = StopReason.Stop
    error_msg = ""

    for event in events:
        if isinstance(event, TextEndEvent):
            text_parts.append(event.text.content)
        elif isinstance(event, EndEvent):
            stop_reason = event.reason
        elif isinstance(event, ErrorEvent):
            stop_reason = event.reason
            error_msg = event.error

    if stop_reason == StopReason.Abort:
        return BranchSummaryResult(aborted=True)
    if stop_reason == StopReason.Error:
        return BranchSummaryResult(error=error_msg or "Summarization failed")

    summary = BRANCH_SUMMARY_PREAMBLE + "".join(text_parts)

    read_files, modified_files = compute_file_lists(preparation.file_ops)
    summary += format_file_operations(read_files, modified_files)

    return BranchSummaryResult(
        summary=summary or "No summary generated",
        read_files=read_files,
        modified_files=modified_files,
    )
