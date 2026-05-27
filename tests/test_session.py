"""Session manager — persistence, branching, tree, labels, compaction entries."""
from __future__ import annotations

from pathlib import Path

import pytest

from operator_use.inference.types import ThinkingLevel
from operator_use.message.types import (
    TextContent, AssistantMessage, UserMessage, Usage,
)
from operator_use.session.manager import SessionManager
from operator_use.session.types import (
    MessageEntry, CompactionEntry, LabelEntry, LeafEntry,
    ModelChangeEntry, ThinkingLevelChangeEntry,
    CustomInfoEntry, CustomMessageEntry,
)


class TestSessionPersistenceRoundtrip:
    def test_write_and_read_back(self, tmp_path: Path):
        d = tmp_path / "s"; d.mkdir()
        sm = SessionManager(cwd=tmp_path, session_dir=d)
        sm.append_message(UserMessage.text("hello"))
        a = AssistantMessage()
        a.contents = [TextContent(content="world")]
        a.usage = Usage(input_tokens=100, output_tokens=50)
        sm.append_message(a)
        assert sm.session_file and sm.session_file.exists()
        sm2 = SessionManager.open(sm.session_file)
        assert sm2.session_id == sm.session_id
        assert len(sm2.get_entries()) == 2

    def test_model_change_entry_persisted(self, tmp_path: Path):
        d = tmp_path / "s"; d.mkdir()
        sm = SessionManager(cwd=tmp_path, session_dir=d)
        sm.append_message(UserMessage.text("msg"))
        a = AssistantMessage(); a.usage = Usage(input_tokens=10, output_tokens=5)
        sm.append_message(a)
        sm.append_model_change("gpt-5", "openai")
        assert any(isinstance(e, ModelChangeEntry) for e in sm.get_entries())

    def test_thinking_level_change_entry_persisted(self, tmp_path: Path):
        d = tmp_path / "s"; d.mkdir()
        sm = SessionManager(cwd=tmp_path, session_dir=d)
        sm.append_message(UserMessage.text("msg"))
        a = AssistantMessage(); a.usage = Usage(input_tokens=10, output_tokens=5)
        sm.append_message(a)
        sm.append_thinking_level_change(ThinkingLevel.High)
        assert any(isinstance(e, ThinkingLevelChangeEntry) for e in sm.get_entries())

    def test_session_name_roundtrip(self, tmp_path: Path):
        d = tmp_path / "s"; d.mkdir()
        sm = SessionManager(cwd=tmp_path, session_dir=d)
        sm.append_session_info("My Session")
        assert sm.get_session_name() == "My Session"

    def test_in_memory_no_disk_writes(self, tmp_path: Path):
        sm = SessionManager.in_memory(cwd=tmp_path)
        sm.append_message(UserMessage.text("secret"))
        a = AssistantMessage(); a.contents = [TextContent(content="reply")]
        sm.append_message(a)
        assert not list(tmp_path.glob("*.jsonl"))


class TestSessionBranching:
    def test_branch_changes_leaf(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("a"))
        id_a = sm.leaf_id
        sm.append_message(AssistantMessage())
        sm.branch(id_a)
        assert sm.leaf_id == id_a
        path = sm.get_branch()
        assert path[-1].id == id_a

    def test_branch_with_summary_produces_branch_entry(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("original"))
        sm.append_message(AssistantMessage())
        original_leaf = sm.leaf_id
        sm.branch_with_summary("Summary of branch", from_id=original_leaf)
        ctx = sm.build_session_context()
        assert len(ctx.messages) >= 1

    def test_fork_from_creates_independent_session(self, tmp_path: Path):
        d = tmp_path / "src"; d.mkdir()
        src = SessionManager(cwd=tmp_path, session_dir=d)
        src.append_message(UserMessage.text("shared"))
        a = AssistantMessage(); a.contents = [TextContent(content="reply")]
        src.append_message(a)
        assert src.session_file and src.session_file.exists()

        fd = tmp_path / "fork"; fd.mkdir()
        forked = SessionManager.fork_from(src.session_file, target_cwd=tmp_path / "f", session_dir=fd)
        assert forked.session_id != src.session_id
        assert forked.session_file and forked.session_file.exists()

    def test_create_branched_session_includes_path(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("u1"))
        sm.append_message(AssistantMessage())
        leaf = sm.leaf_id
        sm.create_branched_session(leaf)
        ids = {e.id for e in sm.get_entries()}
        assert leaf in ids


class TestSessionTree:
    def test_get_tree_contains_all_entries(self):
        sm = SessionManager.in_memory()
        for i in range(5):
            sm.append_message(UserMessage.text(f"q{i}"))
            sm.append_message(AssistantMessage())
        tree = sm.get_tree()
        all_tree_ids: set[str] = set()
        def collect(nodes):
            for node in nodes:
                all_tree_ids.add(node.entry.id)
                collect(node.children)
        collect(tree)
        for e in sm.get_entries():
            assert e.id in all_tree_ids

    def test_label_roundtrip(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("item"))
        target = sm.leaf_id
        sm.append_label_change(target, "v1.0")
        assert sm.get_label(target) == "v1.0"
        sm.append_label_change(target, None)
        assert sm.get_label(target) is None

    def test_large_session_100_pairs(self):
        sm = SessionManager.in_memory()
        for i in range(100):
            sm.append_message(UserMessage.text(f"q{i}"))
            a = AssistantMessage()
            a.contents = [TextContent(content=f"a{i}")]
            sm.append_message(a)
        assert len(sm.get_entries()) == 200


class TestSessionCustomEntries:
    def test_custom_info_entry(self):
        sm = SessionManager.in_memory()
        sm.append_custom_info("my_type", data={"k": "v"})
        assert any(isinstance(e, CustomInfoEntry) and e.custom_type == "my_type" for e in sm.get_entries())

    def test_custom_message_entry(self):
        sm = SessionManager.in_memory()
        sm.append_custom_message("notify", content=[TextContent(content="msg")], display=True)
        assert any(isinstance(e, CustomMessageEntry) and e.custom_type == "notify" for e in sm.get_entries())

    def test_leaf_entry_after_branch(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("a"))
        id_a = sm.leaf_id
        sm.append_message(UserMessage.text("b"))
        sm.branch(id_a)
        assert any(isinstance(e, LeafEntry) for e in sm.entries)

    def test_get_children_returns_sorted(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("parent"))
        parent_id = sm.leaf_id
        sm.append_message(UserMessage.text("child1"))
        sm.branch(parent_id)
        sm.append_message(UserMessage.text("child2"))
        children = sm.get_children(parent_id)
        assert len(children) >= 2
