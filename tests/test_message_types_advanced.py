"""Advanced tests for message/types.py: accessors, factories, from_session conversions."""
import pytest
from pathlib import Path
from operator_use.message.types import (
    TextContent, ImageContent, ThinkingContent, ToolCallContent, ToolResultContent,
    Usage, UserMessage, AssistantMessage, ToolMessage, SystemMessage,
    CustomMessage, BranchSummaryMessage, CompactionSummaryMessage,
    Role,
)
from operator_use.session.types import (
    CustomMessageEntry, BranchEntry, CompactionEntry,
)
from operator_use.session.utils import generate_id, generate_timestamp


# ── UserMessage factories ─────────────────────────────────────────────────────

class TestUserMessageFactories:
    def test_text_factory(self):
        msg = UserMessage.text("hello")
        assert msg.role == Role.USER
        assert len(msg.contents) == 1
        assert isinstance(msg.contents[0], TextContent)
        assert msg.contents[0].content == "hello"

    def test_with_images_factory(self):
        msg = UserMessage.with_images("describe this", ["http://img.png"])
        assert msg.role == Role.USER
        assert len(msg.contents) == 2
        assert isinstance(msg.contents[0], TextContent)
        assert isinstance(msg.contents[1], ImageContent)

    def test_with_images_text_content(self):
        msg = UserMessage.with_images("caption", ["http://img.png"])
        assert msg.contents[0].content == "caption"

    def test_with_images_multiple_images(self):
        msg = UserMessage.with_images("look", ["url1", "url2", "url3"])
        assert msg.contents[1].images == ["url1", "url2", "url3"]

    def test_empty_user_message(self):
        msg = UserMessage()
        assert msg.role == Role.USER
        assert msg.contents == []


# ── SystemMessage ─────────────────────────────────────────────────────────────

class TestSystemMessage:
    def test_text_factory(self):
        msg = SystemMessage.text("You are helpful")
        assert msg.role == Role.SYSTEM
        assert msg.contents[0].content == "You are helpful"


# ── AssistantMessage accessors ────────────────────────────────────────────────

class TestAssistantMessageAccessors:
    def test_text_content_empty_when_no_text(self):
        msg = AssistantMessage()
        assert msg.text_content() == ""

    def test_text_content_single(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="hello")]
        assert msg.text_content() == "hello"

    def test_text_content_concatenates_multiple(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="foo"), TextContent(content="bar")]
        assert msg.text_content() == "foobar"

    def test_text_content_skips_non_text(self):
        msg = AssistantMessage()
        tc = ToolCallContent(id="t1", name="tool", args={})
        msg.contents = [tc, TextContent(content="text")]
        assert msg.text_content() == "text"

    def test_tool_calls_empty_when_none(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="no tools")]
        assert msg.tool_calls() == []

    def test_tool_calls_returns_all(self):
        msg = AssistantMessage()
        tc1 = ToolCallContent(id="t1", name="tool_a", args={})
        tc2 = ToolCallContent(id="t2", name="tool_b", args={})
        msg.contents = [tc1, tc2]
        assert len(msg.tool_calls()) == 2
        assert msg.tool_calls()[0].id == "t1"

    def test_thinking_empty_when_none(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="just text")]
        assert msg.thinking() == []

    def test_thinking_returns_thinking_content(self):
        msg = AssistantMessage()
        th = ThinkingContent(content="let me reason")
        msg.contents = [th, TextContent(content="answer")]
        thoughts = msg.thinking()
        assert len(thoughts) == 1
        assert thoughts[0].content == "let me reason"

    def test_default_stop_reason(self):
        from operator_use.inference.types import StopReason
        msg = AssistantMessage()
        assert msg.stop_reason == StopReason.Stop

    def test_default_usage(self):
        msg = AssistantMessage()
        assert isinstance(msg.usage, Usage)


# ── ToolMessage ───────────────────────────────────────────────────────────────

class TestToolMessage:
    def test_from_result(self):
        rc = ToolResultContent(id="t1", content="output")
        msg = ToolMessage.from_result(rc)
        assert msg.role == Role.TOOL
        assert len(msg.contents) == 1
        assert msg.contents[0].id == "t1"

    def test_from_results_multiple(self):
        r1 = ToolResultContent(id="t1", content="a")
        r2 = ToolResultContent(id="t2", content="b")
        msg = ToolMessage.from_results([r1, r2])
        assert len(msg.contents) == 2

    def test_from_results_copies_list(self):
        original = [ToolResultContent(id="t1", content="x")]
        msg = ToolMessage.from_results(original)
        original.clear()
        # copy — contents unaffected
        assert len(msg.contents) == 1

    def test_from_result_copies_independent(self):
        r = ToolResultContent(id="t1", content="data")
        msg1 = ToolMessage.from_result(r)
        msg2 = ToolMessage.from_result(r)
        assert msg1.contents is not msg2.contents

    def test_tool_result_content_error_flag(self):
        rc = ToolResultContent(id="t1", content="err", is_error=True)
        assert rc.is_error is True

    def test_tool_result_content_defaults(self):
        rc = ToolResultContent(id="t1", content="ok")
        assert rc.is_error is False
        assert rc.metadata == {}


# ── ImageContent ──────────────────────────────────────────────────────────────

class TestImageContent:
    def test_images_stored(self):
        ic = ImageContent(images=["http://a.png", "http://b.png"])
        assert len(ic.images) == 2

    def test_empty_images(self):
        ic = ImageContent()
        assert ic.images == []

    def test_from_url(self):
        ic = ImageContent.from_url("http://example.com/img.png")
        assert len(ic.images) == 1
        assert "http://example.com/img.png" in str(ic.images[0])

    def test_to_base64_with_url_returns_list(self):
        ic = ImageContent(images=["http://a.png"])
        result = ic.to_base64()
        assert isinstance(result, list)


# ── CustomMessage.from_session ────────────────────────────────────────────────

class TestCustomMessageFromSession:
    def _make_entry(self, content, custom_type="event", details=None):
        # Use model_construct to bypass Pydantic validation for testing from_session
        # edge cases (string/int content) that aren't normally stored but handled for compat.
        return CustomMessageEntry.model_construct(
            id=generate_id(set()),
            parent_id=None,
            timestamp=generate_timestamp(),
            custom_type=custom_type,
            content=content,
            display=True,
            details=details,
        )

    def test_string_content_becomes_text_content(self):
        entry = self._make_entry("hello world")
        msg = CustomMessage.from_session(entry)
        assert len(msg.contents) == 1
        assert isinstance(msg.contents[0], TextContent)
        assert msg.contents[0].content == "hello world"

    def test_list_content_used_directly(self):
        content = [TextContent(content="item1"), TextContent(content="item2")]
        entry = self._make_entry(content)
        msg = CustomMessage.from_session(entry)
        assert len(msg.contents) == 2

    def test_non_string_non_list_gives_empty_contents(self):
        entry = self._make_entry(42)
        msg = CustomMessage.from_session(entry)
        assert msg.contents == []

    def test_custom_type_preserved(self):
        entry = self._make_entry("data", custom_type="special_event")
        msg = CustomMessage.from_session(entry)
        assert msg.custom_type == "special_event"

    def test_details_preserved(self):
        entry = self._make_entry("x", details={"key": "val"})
        msg = CustomMessage.from_session(entry)
        assert msg.details == {"key": "val"}

    def test_role_is_custom(self):
        entry = self._make_entry("x")
        msg = CustomMessage.from_session(entry)
        assert msg.role == Role.CUSTOM


# ── BranchSummaryMessage.from_session ─────────────────────────────────────────

class TestBranchSummaryMessageFromSession:
    def _make_entry(self, summary="branch summary"):
        return BranchEntry(
            id=generate_id(set()),
            parent_id=None,
            timestamp=generate_timestamp(),
            from_id="root-id",
            summary=summary,
        )

    def test_summary_preserved(self):
        entry = self._make_entry("archived branch work")
        msg = BranchSummaryMessage.from_session(entry)
        assert msg.summary == "archived branch work"

    def test_from_id_preserved(self):
        entry = self._make_entry()
        msg = BranchSummaryMessage.from_session(entry)
        assert msg.from_id == "root-id"

    def test_timestamp_preserved(self):
        entry = self._make_entry()
        msg = BranchSummaryMessage.from_session(entry)
        assert msg.timestamp == entry.timestamp

    def test_role_is_branch_summary(self):
        entry = self._make_entry()
        msg = BranchSummaryMessage.from_session(entry)
        assert msg.role == Role.BRANCH_SUMMARY


# ── CompactionSummaryMessage.from_session ─────────────────────────────────────

class TestCompactionSummaryMessageFromSession:
    def _make_entry(self, summary="compaction summary"):
        return CompactionEntry(
            id=generate_id(set()),
            parent_id=None,
            timestamp=generate_timestamp(),
            summary=summary,
            first_kept_entry_id="kept-id",
            tokens_before=1000,
        )

    def test_summary_preserved(self):
        entry = self._make_entry("history condensed")
        msg = CompactionSummaryMessage.from_session(entry)
        assert msg.summary == "history condensed"

    def test_tokens_before_preserved(self):
        entry = self._make_entry()
        msg = CompactionSummaryMessage.from_session(entry)
        assert msg.tokens_before == 1000

    def test_timestamp_preserved(self):
        entry = self._make_entry()
        msg = CompactionSummaryMessage.from_session(entry)
        assert msg.timestamp == entry.timestamp

    def test_role_is_compaction_summary(self):
        entry = self._make_entry()
        msg = CompactionSummaryMessage.from_session(entry)
        assert msg.role == Role.COMPACTION_SUMMARY


# ── ToolCallContent ───────────────────────────────────────────────────────────

class TestToolCallContent:
    def test_default_args_empty(self):
        tc = ToolCallContent(id="t1", name="tool")
        assert tc.args == {}

    def test_args_stored(self):
        tc = ToolCallContent(id="t1", name="tool", args={"key": "value"})
        assert tc.args["key"] == "value"

    def test_id_and_name(self):
        tc = ToolCallContent(id="abc", name="my_tool")
        assert tc.id == "abc"
        assert tc.name == "my_tool"
