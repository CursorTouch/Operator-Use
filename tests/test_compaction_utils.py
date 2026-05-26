"""Tests for compaction/utils.py: token estimation, file ops, cut points, serialization."""
import pytest
from program.compaction.strategy.utils import (
    calculate_context_tokens,
    estimate_tokens,
    get_assistant_usage,
    get_last_assistant_usage,
    get_last_assistant_usage_info,
    estimate_context_tokens,
    extract_file_ops_from_message,
    compute_file_lists,
    format_file_operations,
    get_message_from_entry,
    get_message_from_entry_for_compaction,
    find_valid_cut_points,
    find_turn_start_index,
    find_cut_point,
    build_summary_prompt,
    extract_text_from_events,
    find_prev_compaction_index,
    resolve_boundary_start,
    collect_messages_in_range,
    serialize_conversation,
    _truncate_for_summary,
)
from program.compaction.strategy.types import FileOperations
from program.message.types import (
    UserMessage, AssistantMessage, ToolMessage,
    TextContent, ThinkingContent, ToolCallContent, ToolResultContent,
    BranchSummaryMessage, CompactionSummaryMessage, Usage, Role,
)
from program.session.manager import SessionManager
from program.session.types import MessageEntry, CompactionEntry
from program.inference.types import (
    StopReason, TextEndEvent, EndEvent, ErrorEvent, StartEvent,
    TextStartEvent, TextDeltaEvent,
)
from program.tool.types import ToolKind


# ── calculate_context_tokens ──────────────────────────────────────────────────

class TestCalculateContextTokens:
    def test_sums_all_token_fields(self):
        u = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=20, cache_write_tokens=10)
        assert calculate_context_tokens(u) == 180

    def test_zero_usage(self):
        u = Usage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
        assert calculate_context_tokens(u) == 0


# ── estimate_tokens ───────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_user_text_message(self):
        msg = UserMessage.text("hello world")
        tokens = estimate_tokens(msg)
        assert tokens >= 1

    def test_user_image_counts_fixed_amount(self):
        from program.message.types import ImageContent
        msg = UserMessage()
        msg.contents = [ImageContent(images=["http://img.png"])]
        tokens = estimate_tokens(msg)
        assert tokens == 4800 // 4

    def test_assistant_text(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="a" * 400)]
        assert estimate_tokens(msg) == 100

    def test_assistant_thinking(self):
        msg = AssistantMessage()
        msg.contents = [ThinkingContent(content="t" * 400)]
        assert estimate_tokens(msg) == 100

    def test_tool_message(self):
        msg = ToolMessage()
        msg.contents = [ToolResultContent(id="x", content="r" * 400)]
        assert estimate_tokens(msg) == 100

    def test_minimum_one_token(self):
        msg = UserMessage.text("")
        assert estimate_tokens(msg) >= 1

    def test_branch_summary_message(self):
        msg = BranchSummaryMessage(summary="s" * 400, from_id="x", timestamp=0.0)
        assert estimate_tokens(msg) == 100

    def test_compaction_summary_message(self):
        msg = CompactionSummaryMessage(summary="c" * 400, tokens_before=0, timestamp=0.0)
        assert estimate_tokens(msg) == 100


# ── get_assistant_usage ───────────────────────────────────────────────────────

class TestGetAssistantUsage:
    def test_returns_usage_for_stop_reason(self):
        msg = AssistantMessage()
        msg.stop_reason = StopReason.Stop
        msg.usage = Usage(input_tokens=10, output_tokens=5)
        assert get_assistant_usage(msg) is not None

    def test_returns_none_for_error_reason(self):
        msg = AssistantMessage()
        msg.stop_reason = StopReason.Error
        msg.usage = Usage(input_tokens=10, output_tokens=5)
        assert get_assistant_usage(msg) is None

    def test_returns_none_for_user_message(self):
        assert get_assistant_usage(UserMessage.text("hi")) is None

    def test_returns_none_when_no_usage(self):
        msg = AssistantMessage()
        msg.stop_reason = StopReason.Stop
        msg.usage = None
        assert get_assistant_usage(msg) is None


# ── get_last_assistant_usage ──────────────────────────────────────────────────

class TestGetLastAssistantUsage:
    def _make_entries(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("q"))
        msg = AssistantMessage()
        msg.stop_reason = StopReason.Stop
        msg.usage = Usage(input_tokens=50, output_tokens=20)
        msg.contents = [TextContent(content="ans")]
        sm.append_message(msg)
        return sm.get_entries()

    def test_finds_last_usage(self):
        entries = self._make_entries()
        usage = get_last_assistant_usage(entries)
        assert usage is not None
        assert usage.input_tokens == 50

    def test_returns_none_for_empty(self):
        assert get_last_assistant_usage([]) is None


# ── estimate_context_tokens ───────────────────────────────────────────────────

class TestEstimateContextTokens:
    def test_fallback_when_no_usage(self):
        msgs = [UserMessage.text("hello"), UserMessage.text("world")]
        result = estimate_context_tokens(msgs)
        assert result.tokens > 0
        assert result.last_usage_index is None

    def test_uses_last_assistant_usage(self):
        msgs = []
        msgs.append(UserMessage.text("q"))
        msg = AssistantMessage()
        msg.stop_reason = StopReason.Stop
        msg.usage = Usage(input_tokens=100, output_tokens=50)
        msg.contents = [TextContent(content="ans")]
        msgs.append(msg)
        result = estimate_context_tokens(msgs)
        assert result.usage_tokens == 150
        assert result.last_usage_index == 1


# ── file operations ───────────────────────────────────────────────────────────

class TestFileOperations:
    def _tool_call_msg(self, kind: ToolKind, path: str) -> AssistantMessage:
        msg = AssistantMessage()
        tc = ToolCallContent(id="t1", name="tool", args={"path": path})
        tc.kind = kind
        msg.contents = [tc]
        return msg

    def test_extract_read_op(self):
        ops = FileOperations()
        msg = self._tool_call_msg(ToolKind.Read, "/a.py")
        extract_file_ops_from_message(msg, ops)
        assert "/a.py" in ops.read

    def test_extract_write_op(self):
        ops = FileOperations()
        msg = self._tool_call_msg(ToolKind.Write, "/b.py")
        extract_file_ops_from_message(msg, ops)
        assert "/b.py" in ops.written

    def test_extract_edit_op(self):
        ops = FileOperations()
        msg = self._tool_call_msg(ToolKind.Edit, "/c.py")
        extract_file_ops_from_message(msg, ops)
        assert "/c.py" in ops.edited

    def test_non_assistant_message_ignored(self):
        ops = FileOperations()
        extract_file_ops_from_message(UserMessage.text("hi"), ops)
        assert len(ops.read) == 0

    def test_compute_file_lists_separates_modified(self):
        ops = FileOperations(read={"/a.py", "/b.py"}, edited={"/b.py"}, written={"/c.py"})
        read_files, modified = compute_file_lists(ops)
        assert "/a.py" in read_files
        assert "/b.py" not in read_files  # edited, so in modified
        assert "/b.py" in modified
        assert "/c.py" in modified

    def test_format_file_operations_empty(self):
        assert format_file_operations([], []) == ""

    def test_format_file_operations_read_only(self):
        out = format_file_operations(["/a.py"], [])
        assert "<read-files>" in out
        assert "/a.py" in out

    def test_format_file_operations_modified_only(self):
        out = format_file_operations([], ["/b.py"])
        assert "<modified-files>" in out

    def test_format_file_operations_both(self):
        out = format_file_operations(["/a.py"], ["/b.py"])
        assert "<read-files>" in out
        assert "<modified-files>" in out


# ── get_message_from_entry ────────────────────────────────────────────────────

class TestGetMessageFromEntry:
    def test_message_entry_returns_message(self):
        sm = SessionManager.in_memory()
        eid = sm.append_message(UserMessage.text("hi"))
        entry = sm.by_id[eid]
        msg = get_message_from_entry(entry)
        assert msg is not None
        assert msg.role == Role.USER

    def test_compaction_entry_returns_summary(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(UserMessage.text("a"))
        sm.append_compaction("sum", id1, 100)
        entries = sm.get_entries()
        comp_entry = next(e for e in entries if isinstance(e, CompactionEntry))
        msg = get_message_from_entry(comp_entry)
        assert msg is not None

    def test_compaction_skipped_for_compaction(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(UserMessage.text("a"))
        sm.append_compaction("sum", id1, 100)
        entries = sm.get_entries()
        comp_entry = next(e for e in entries if isinstance(e, CompactionEntry))
        assert get_message_from_entry_for_compaction(comp_entry) is None


# ── find_valid_cut_points ─────────────────────────────────────────────────────

class TestFindValidCutPoints:
    def _make_entries(self, n_user=3, n_asst=2):
        sm = SessionManager.in_memory()
        for i in range(max(n_user, n_asst)):
            if i < n_user:
                sm.append_message(UserMessage.text(f"u{i}"))
            if i < n_asst:
                msg = AssistantMessage()
                msg.contents = [TextContent(content=f"a{i}")]
                sm.append_message(msg)
        return sm.get_entries()

    def test_finds_user_and_assistant(self):
        entries = self._make_entries(3, 2)
        cut_points = find_valid_cut_points(entries, 0, len(entries))
        assert len(cut_points) > 0

    def test_empty_range_returns_empty(self):
        entries = self._make_entries(3, 2)
        cut_points = find_valid_cut_points(entries, 2, 2)
        assert cut_points == []


# ── find_cut_point ────────────────────────────────────────────────────────────

class TestFindCutPoint:
    def _make_entries(self):
        sm = SessionManager.in_memory()
        for i in range(5):
            sm.append_message(UserMessage.text(f"u{i}"))
            msg = AssistantMessage()
            msg.contents = [TextContent(content=f"a{i}")]
            sm.append_message(msg)
        return sm.get_entries()

    def test_returns_cut_point_result(self):
        entries = self._make_entries()
        result = find_cut_point(entries, 0, len(entries), keep_recent_tokens=1)
        assert result.first_kept_entry_index >= 0

    def test_empty_entries_returns_start(self):
        sm = SessionManager.in_memory()
        entries = sm.get_entries()
        result = find_cut_point(entries, 0, len(entries), keep_recent_tokens=1)
        assert result.first_kept_entry_index == 0


# ── find_prev_compaction_index / resolve_boundary_start ──────────────────────

class TestCompactionBoundary:
    def test_no_compaction_returns_minus_one(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("hi"))
        entries = sm.get_entries()
        assert find_prev_compaction_index(entries) == -1

    def test_finds_compaction_index(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(UserMessage.text("a"))
        sm.append_compaction("sum", id1, 100)
        sm.append_message(UserMessage.text("b"))
        entries = sm.get_entries()
        idx = find_prev_compaction_index(entries)
        assert idx >= 0
        assert isinstance(entries[idx], CompactionEntry)

    def test_resolve_boundary_no_compaction(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("x"))
        entries = sm.get_entries()
        assert resolve_boundary_start(entries, -1) == 0

    def test_resolve_boundary_after_compaction(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(UserMessage.text("a"))
        id2 = sm.append_message(UserMessage.text("b"))
        sm.append_compaction("sum", id2, 100)
        sm.append_message(UserMessage.text("c"))
        entries = sm.get_entries()
        idx = find_prev_compaction_index(entries)
        start = resolve_boundary_start(entries, idx)
        assert start >= 0


# ── collect_messages_in_range ─────────────────────────────────────────────────

class TestCollectMessagesInRange:
    def test_collects_all_messages(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("a"))
        sm.append_message(UserMessage.text("b"))
        entries = sm.get_entries()
        msgs = collect_messages_in_range(entries, 0, len(entries))
        assert len(msgs) == 2

    def test_for_compaction_skips_compaction_entries(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(UserMessage.text("a"))
        sm.append_compaction("sum", id1, 100)
        sm.append_message(UserMessage.text("b"))
        entries = sm.get_entries()
        msgs = collect_messages_in_range(entries, 0, len(entries), for_compaction=True)
        # compaction summary should be excluded
        roles = [m.role for m in msgs]
        assert Role.COMPACTION_SUMMARY not in roles


# ── build_summary_prompt ──────────────────────────────────────────────────────

class TestBuildSummaryPrompt:
    def test_uses_base_prompt_without_summary(self):
        msgs = [UserMessage.text("hi")]
        result = build_summary_prompt(msgs, None, None, "BASE", "UPDATE")
        assert "BASE" in result

    def test_uses_update_prompt_with_summary(self):
        msgs = [UserMessage.text("hi")]
        result = build_summary_prompt(msgs, "prior summary", None, "BASE", "UPDATE")
        assert "UPDATE" in result
        assert "prior summary" in result

    def test_custom_instructions_appended(self):
        msgs = [UserMessage.text("hi")]
        result = build_summary_prompt(msgs, None, "focus on X", "BASE", "UPDATE")
        assert "focus on X" in result

    def test_conversation_wrapped_in_xml(self):
        msgs = [UserMessage.text("hello")]
        result = build_summary_prompt(msgs, None, None, "BASE", "UPDATE")
        assert "<conversation>" in result


# ── extract_text_from_events ──────────────────────────────────────────────────

class TestExtractTextFromEvents:
    def test_extracts_text(self):
        events = [
            StartEvent(),
            TextStartEvent(text=TextContent(content="")),
            TextDeltaEvent(text=TextContent(content="hello")),
            TextEndEvent(text=TextContent(content="hello")),
            EndEvent(reason=StopReason.Stop),
        ]
        result = extract_text_from_events(events, "test")
        assert result == "hello"

    def test_raises_on_error_event(self):
        events = [
            StartEvent(),
            ErrorEvent(reason=StopReason.Error, error="boom"),
        ]
        with pytest.raises(RuntimeError, match="boom"):
            extract_text_from_events(events, "test")

    def test_unknown_error_uses_fallback(self):
        events = [ErrorEvent(reason=StopReason.Error, error="")]
        with pytest.raises(RuntimeError, match="Unknown error"):
            extract_text_from_events(events, "label")


# ── serialize_conversation ────────────────────────────────────────────────────

class TestSerializeConversation:
    def test_user_message(self):
        out = serialize_conversation([UserMessage.text("hello")])
        assert "[User]: hello" in out

    def test_assistant_message(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="world")]
        out = serialize_conversation([msg])
        assert "[Assistant]: world" in out

    def test_tool_message(self):
        msg = ToolMessage()
        msg.contents = [ToolResultContent(id="t1", content="tool output")]
        out = serialize_conversation([msg])
        assert "[Tool result]: tool output" in out

    def test_assistant_thinking(self):
        msg = AssistantMessage()
        msg.contents = [ThinkingContent(content="ponder")]
        out = serialize_conversation([msg])
        assert "[Assistant thinking]: ponder" in out

    def test_tool_call_serialized(self):
        msg = AssistantMessage()
        tc = ToolCallContent(id="t1", name="my_tool", args={"x": 1})
        msg.contents = [tc]
        out = serialize_conversation([msg])
        assert "my_tool" in out

    def test_empty_list(self):
        assert serialize_conversation([]) == ""

    def test_truncation_in_tool_result(self):
        long_text = "x" * 3000
        msg = ToolMessage()
        msg.contents = [ToolResultContent(id="t1", content=long_text)]
        out = serialize_conversation([msg])
        assert "truncated" in out

    def test_truncate_helper(self):
        short = "abc"
        assert _truncate_for_summary(short, 100) == short
        long = "x" * 3000
        result = _truncate_for_summary(long, 2000)
        assert "truncated" in result
        assert len(result) < len(long)
