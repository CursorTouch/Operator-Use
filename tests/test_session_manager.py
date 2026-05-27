"""Tests for SessionManager: in-memory operations, branching, compaction, context building."""
import pytest
from pathlib import Path
from operator_use.session.manager import SessionManager
from operator_use.session.types import (
    MessageEntry, CompactionEntry, BranchEntry, LabelEntry,
    ThinkingLevelChangeEntry, ModelChangeEntry,
)
from operator_use.message.types import UserMessage, AssistantMessage, TextContent, Role
from operator_use.inference.types import ThinkingLevel


def make_sm() -> SessionManager:
    return SessionManager.in_memory()


def user_msg(text: str = "hello") -> UserMessage:
    return UserMessage.text(text)


def assistant_msg(text: str = "hi") -> AssistantMessage:
    msg = AssistantMessage()
    msg.contents = [TextContent(content=text)]
    return msg


# ── Initialisation ────────────────────────────────────────────────────────────

class TestInit:
    def test_new_session_has_id(self):
        sm = make_sm()
        assert sm.session_id is not None

    def test_leaf_starts_none(self):
        sm = make_sm()
        assert sm.leaf_id is None

    def test_entries_has_header(self):
        sm = make_sm()
        assert len(sm.entries) == 1  # just the header

    def test_in_memory_does_not_persist(self):
        sm = make_sm()
        assert sm.session_file is None


# ── append_message ────────────────────────────────────────────────────────────

class TestAppendMessage:
    def test_append_increments_entries(self):
        sm = make_sm()
        sm.append_message(user_msg())
        assert len(sm.get_entries()) == 1

    def test_append_updates_leaf_id(self):
        sm = make_sm()
        eid = sm.append_message(user_msg())
        assert sm.leaf_id == eid

    def test_two_messages_linked(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("first"))
        id2 = sm.append_message(assistant_msg("second"))
        entry2 = sm.by_id[id2]
        assert entry2.parent_id == id1

    def test_messages_retrievable_by_id(self):
        sm = make_sm()
        eid = sm.append_message(user_msg("test"))
        entry = sm.get_entry(eid)
        assert isinstance(entry, MessageEntry)
        assert entry.message.role == Role.USER

    def test_append_multiple(self):
        sm = make_sm()
        for i in range(5):
            sm.append_message(user_msg(str(i)))
        assert len(sm.get_entries()) == 5


# ── build_session_context ─────────────────────────────────────────────────────

class TestBuildSessionContext:
    def test_empty_session(self):
        sm = make_sm()
        ctx = sm.build_session_context()
        assert ctx.messages == []

    def test_messages_in_order(self):
        sm = make_sm()
        sm.append_message(user_msg("q1"))
        sm.append_message(assistant_msg("a1"))
        sm.append_message(user_msg("q2"))
        ctx = sm.build_session_context()
        roles = [m.role for m in ctx.messages]
        assert roles == [Role.USER, Role.ASSISTANT, Role.USER]

    def test_thinking_level_tracked(self):
        sm = make_sm()
        sm.append_thinking_level_change(ThinkingLevel.High)
        ctx = sm.build_session_context()
        assert ctx.thinking_level == ThinkingLevel.High

    def test_model_change_tracked(self):
        sm = make_sm()
        sm.append_model_change("claude-opus-4-7", "anthropic")
        ctx = sm.build_session_context()
        assert ctx.model_id == "claude-opus-4-7"
        assert ctx.provider_id == "anthropic"

    def test_latest_thinking_level_wins(self):
        sm = make_sm()
        sm.append_thinking_level_change(ThinkingLevel.Low)
        sm.append_thinking_level_change(ThinkingLevel.Max)
        ctx = sm.build_session_context()
        assert ctx.thinking_level == ThinkingLevel.Max

    def test_context_with_compaction_includes_summary(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("old"))
        id2 = sm.append_message(assistant_msg("old reply"))
        id3 = sm.append_message(user_msg("new"))
        # Compact: retain from id3 onward
        sm.append_compaction(
            summary="summary of old",
            first_kept_entry_id=id3,
            tokens_before=100,
        )
        sm.append_message(user_msg("after compact"))

        ctx = sm.build_session_context()
        roles = [m.role for m in ctx.messages]
        # Should have: compaction summary, "new" user msg, "after compact" user msg
        from operator_use.message.types import Role
        assert Role.COMPACTION_SUMMARY in roles

    def test_context_without_compaction_has_all_messages(self):
        sm = make_sm()
        sm.append_message(user_msg("a"))
        sm.append_message(assistant_msg("b"))
        sm.append_message(user_msg("c"))
        ctx = sm.build_session_context()
        assert len(ctx.messages) == 3


# ── get_branch ────────────────────────────────────────────────────────────────

class TestGetBranch:
    def test_branch_to_leaf(self):
        sm = make_sm()
        sm.append_message(user_msg("a"))
        id2 = sm.append_message(user_msg("b"))
        sm.append_message(user_msg("c"))
        path = sm.get_branch()
        assert path[-1].id == sm.leaf_id

    def test_branch_from_specific_id(self):
        sm = make_sm()
        sm.append_message(user_msg("a"))
        id2 = sm.append_message(user_msg("b"))
        sm.append_message(user_msg("c"))
        path = sm.get_branch(from_id=id2)
        assert len(path) == 2
        assert path[-1].id == id2

    def test_empty_session_branch(self):
        sm = make_sm()
        assert sm.get_branch() == []


# ── branching ─────────────────────────────────────────────────────────────────

class TestBranching:
    def test_branch_changes_leaf(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("a"))
        sm.append_message(user_msg("b"))
        sm.branch(id1)
        assert sm.leaf_id == id1

    def test_branch_invalid_id_raises(self):
        sm = make_sm()
        with pytest.raises(KeyError):
            sm.branch("nonexistent-id")

    def test_branch_with_summary_creates_entry(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("a"))
        bid = sm.branch_with_summary("summary of a", from_id=id1)
        entry = sm.by_id[bid]
        assert isinstance(entry, BranchEntry)
        assert entry.summary == "summary of a"

    def test_append_after_branch_goes_to_new_leaf(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("a"))
        sm.append_message(user_msg("b"))
        sm.branch(id1)
        id_new = sm.append_message(user_msg("c"))
        assert sm.leaf_id == id_new
        entry = sm.by_id[id_new]
        assert entry.parent_id == id1


# ── labels ────────────────────────────────────────────────────────────────────

class TestLabels:
    def test_append_label(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg())
        sm.append_label_change(id1, "checkpoint")
        assert sm.get_label(id1) == "checkpoint"

    def test_remove_label(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg())
        sm.append_label_change(id1, "v1")
        sm.append_label_change(id1, None)
        assert sm.get_label(id1) is None

    def test_overwrite_label(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg())
        sm.append_label_change(id1, "first")
        sm.append_label_change(id1, "second")
        assert sm.get_label(id1) == "second"


# ── get_tree ──────────────────────────────────────────────────────────────────

class TestGetTree:
    def test_linear_tree(self):
        sm = make_sm()
        sm.append_message(user_msg("a"))
        sm.append_message(user_msg("b"))
        tree = sm.get_tree()
        assert len(tree) == 1  # single root node
        assert len(tree[0].children) == 1

    def test_branched_tree(self):
        sm = make_sm()
        id1 = sm.append_message(user_msg("root"))
        sm.append_message(user_msg("branch_a"))
        sm.branch(id1)
        sm.append_message(user_msg("branch_b"))
        tree = sm.get_tree()
        # root entry has 2 children
        assert len(tree[0].children) == 2
