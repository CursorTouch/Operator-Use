"""Tests for SessionManager file persistence: create, open, fork_from, branched sessions."""
import pytest
from pathlib import Path
from program.session.manager import SessionManager
from program.session.types import MessageEntry, BranchEntry, CompactionEntry, SessionHeader
from program.session.utils import read_session_file
from program.message.types import UserMessage, AssistantMessage, TextContent, Role


def user(text: str = "hello") -> UserMessage:
    return UserMessage.text(text)


def assistant(text: str = "hi") -> AssistantMessage:
    msg = AssistantMessage()
    msg.contents = [TextContent(content=text)]
    return msg


# ── create() ─────────────────────────────────────────────────────────────────

class TestCreate:
    def test_creates_session_dir(self, tmp_path):
        sm = SessionManager.create(tmp_path / "project", session_dir=tmp_path / "sessions")
        assert (tmp_path / "sessions").is_dir()

    def test_new_session_has_id(self, tmp_path):
        sm = SessionManager.create(tmp_path / "project", session_dir=tmp_path / "sessions")
        assert sm.session_id is not None

    def test_session_file_created_after_persist(self, tmp_path):
        sm = SessionManager.create(tmp_path / "project", session_dir=tmp_path / "sessions")
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        # File should exist now (assistant present → persisted)
        assert sm.session_file is not None
        assert sm.session_file.exists()

    def test_file_not_created_without_assistant(self, tmp_path):
        sm = SessionManager.create(tmp_path / "project", session_dir=tmp_path / "sessions")
        sm.append_message(user("q"))
        # No assistant yet → file not flushed
        if sm.session_file and sm.session_file.exists():
            content = sm.session_file.read_text()
            assert "assistant" not in content


# ── open() ────────────────────────────────────────────────────────────────────

class TestOpen:
    def _write_session(self, path: Path) -> SessionManager:
        sm = SessionManager.create(path.parent, session_dir=path.parent / "sessions")
        sm.append_message(user("hello"))
        sm.append_message(assistant("world"))
        return sm

    def test_open_loads_existing_file(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "sess")
        sm1.append_message(user("q"))
        sm1.append_message(assistant("a"))
        session_file = sm1.session_file
        assert session_file is not None and session_file.exists()

        sm2 = SessionManager.open(session_file, session_dir=tmp_path / "sess")
        entries = sm2.get_entries()
        assert len(entries) >= 1

    def test_open_preserves_session_id(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "sess")
        sm1.append_message(user("q"))
        sm1.append_message(assistant("a"))
        original_id = sm1.session_id

        sm2 = SessionManager.open(sm1.session_file, session_dir=tmp_path / "sess")
        assert sm2.session_id == original_id

    def test_open_preserves_messages(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "sess")
        sm1.append_message(user("first question"))
        sm1.append_message(assistant("first answer"))
        sm1.append_message(user("second question"))
        sm1.append_message(assistant("second answer"))

        sm2 = SessionManager.open(sm1.session_file, session_dir=tmp_path / "sess")
        msg_entries = [e for e in sm2.get_entries() if isinstance(e, MessageEntry)]
        assert len(msg_entries) == 4

    def test_open_missing_file_raises(self, tmp_path):
        ghost = tmp_path / "does_not_exist.jsonl"
        with pytest.raises(ValueError, match="No header"):
            SessionManager.open(ghost, session_dir=tmp_path)


# ── persist round-trip ────────────────────────────────────────────────────────

class TestPersistRoundTrip:
    def test_messages_survive_reopen(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm1.append_message(user("saved q"))
        sm1.append_message(assistant("saved a"))
        path = sm1.session_file

        sm2 = SessionManager.open(path, session_dir=tmp_path / "s")
        texts = []
        for e in sm2.get_entries():
            if isinstance(e, MessageEntry):
                for c in e.message.contents:
                    if hasattr(c, "content"):
                        texts.append(c.content)
        assert "saved q" in texts
        assert "saved a" in texts

    def test_compaction_entry_survives_reopen(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm1.append_message(user("q"))
        sm1.append_message(assistant("a"))
        first_id = sm1.get_entries()[0].id
        sm1.append_compaction("compacted summary", first_id, 500)
        path = sm1.session_file

        sm2 = SessionManager.open(path, session_dir=tmp_path / "s")
        comp_entries = [e for e in sm2.get_entries() if isinstance(e, CompactionEntry)]
        assert len(comp_entries) == 1
        assert "compacted summary" in comp_entries[0].summary

    def test_new_messages_appended_after_flush(self, tmp_path):
        sm = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm.append_message(user("q1"))
        sm.append_message(assistant("a1"))
        # File is now flushed with 2 messages
        sm.append_message(user("q2"))
        sm.append_message(assistant("a2"))

        sm2 = SessionManager.open(sm.session_file, session_dir=tmp_path / "s")
        msg_entries = [e for e in sm2.get_entries() if isinstance(e, MessageEntry)]
        assert len(msg_entries) == 4


# ── get_children ─────────────────────────────────────────────────────────────

class TestGetChildren:
    def test_no_children_initially(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(user("a"))
        assert sm.get_children(id1) == []

    def test_returns_direct_children(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(user("root"))
        id2 = sm.append_message(user("child1"))
        sm.branch(id1)
        id3 = sm.append_message(user("child2"))
        children = sm.get_children(id1)
        child_ids = {c.id for c in children}
        assert child_ids == {id2, id3}

    def test_children_ordered_by_timestamp(self):
        sm = SessionManager.in_memory()
        id1 = sm.append_message(user("root"))
        id2 = sm.append_message(user("first"))
        sm.branch(id1)
        import time; time.sleep(0.01)
        id3 = sm.append_message(user("second"))
        children = sm.get_children(id1)
        assert children[0].id == id2
        assert children[1].id == id3


# ── append_custom_message / append_session_info ───────────────────────────────

class TestCustomEntries:
    def test_append_custom_message(self):
        from program.message.types import TextContent
        sm = SessionManager.in_memory()
        eid = sm.append_custom_message("tool_result", [TextContent(content="result data")])
        entry = sm.get_entry(eid)
        from program.session.types import CustomMessageEntry
        assert isinstance(entry, CustomMessageEntry)
        assert entry.custom_type == "tool_result"

    def test_custom_message_with_details(self):
        from program.message.types import TextContent
        sm = SessionManager.in_memory()
        eid = sm.append_custom_message("event", [TextContent(content="x")], details={"meta": 1})
        entry = sm.get_entry(eid)
        assert entry.details == {"meta": 1}

    def test_append_session_info(self):
        sm = SessionManager.in_memory()
        sm.append_session_info("My Session Name")
        assert sm.get_session_name() == "My Session Name"

    def test_get_session_name_returns_latest(self):
        sm = SessionManager.in_memory()
        sm.append_session_info("First Name")
        sm.append_session_info("Final Name")
        assert sm.get_session_name() == "Final Name"

    def test_get_session_name_returns_none_when_empty(self):
        sm = SessionManager.in_memory()
        assert sm.get_session_name() is None

    def test_append_custom_info(self):
        sm = SessionManager.in_memory()
        from program.session.types import CustomInfoEntry
        eid = sm.append_custom_info("log_entry", {"msg": "hello"})
        entry = sm.get_entry(eid)
        assert isinstance(entry, CustomInfoEntry)
        assert entry.custom_type == "log_entry"


# ── fork_from ─────────────────────────────────────────────────────────────────

class TestForkFrom:
    def test_fork_creates_new_session_id(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm1.append_message(user("q"))
        sm1.append_message(assistant("a"))
        assert sm1.session_file is not None

        sm2 = SessionManager.fork_from(
            sm1.session_file, target_cwd=tmp_path, session_dir=tmp_path / "s2"
        )
        assert sm2.session_id != sm1.session_id

    def test_fork_copies_all_messages(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm1.append_message(user("q1"))
        sm1.append_message(assistant("a1"))
        sm1.append_message(user("q2"))
        sm1.append_message(assistant("a2"))

        sm2 = SessionManager.fork_from(
            sm1.session_file, target_cwd=tmp_path, session_dir=tmp_path / "s2"
        )
        msg_entries = [e for e in sm2.get_entries() if isinstance(e, MessageEntry)]
        assert len(msg_entries) == 4

    def test_fork_is_independent_from_source(self, tmp_path):
        sm1 = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm1.append_message(user("q"))
        sm1.append_message(assistant("a"))

        sm2 = SessionManager.fork_from(
            sm1.session_file, target_cwd=tmp_path, session_dir=tmp_path / "s2"
        )
        sm2.append_message(user("fork q"))
        sm2.append_message(assistant("fork a"))

        # Original not affected
        sm1_reloaded = SessionManager.open(sm1.session_file, session_dir=tmp_path / "s")
        original_entries = [e for e in sm1_reloaded.get_entries() if isinstance(e, MessageEntry)]
        assert len(original_entries) == 2

    def test_fork_missing_source_raises(self, tmp_path):
        with pytest.raises((ValueError, Exception)):
            SessionManager.fork_from(
                tmp_path / "ghost.jsonl", target_cwd=tmp_path, session_dir=tmp_path / "s"
            )


# ── create_branched_session ───────────────────────────────────────────────────

class TestCreateBranchedSession:
    def test_creates_new_session_id(self, tmp_path):
        sm = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        old_id = sm.session_id
        sm.create_branched_session(sm.leaf_id)
        assert sm.session_id != old_id

    def test_branched_session_preserves_path(self, tmp_path):
        sm = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm.append_message(user("q1"))
        sm.append_message(assistant("a1"))
        sm.append_message(user("q2"))
        sm.append_message(assistant("a2"))
        target_id = sm.get_entries()[1].id  # second entry

        sm.create_branched_session(target_id)
        msg_entries = [e for e in sm.get_entries() if isinstance(e, MessageEntry)]
        assert len(msg_entries) == 2

    def test_branched_session_labels_preserved(self, tmp_path):
        sm = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        id1 = sm.append_message(user("q"))
        id2 = sm.append_message(assistant("a"))
        sm.append_label_change(id1, "my-label")

        sm.create_branched_session(id2)
        assert sm.get_label(id1) == "my-label"


# ── new_session() ─────────────────────────────────────────────────────────────

class TestNewSession:
    def test_new_session_resets_entries(self):
        sm = SessionManager.in_memory()
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        assert len(sm.get_entries()) == 2
        sm.new_session()
        assert len(sm.get_entries()) == 0

    def test_new_session_new_id(self):
        sm = SessionManager.in_memory()
        old_id = sm.session_id
        sm.new_session()
        assert sm.session_id != old_id

    def test_new_session_clears_leaf(self):
        sm = SessionManager.in_memory()
        sm.append_message(user("q"))
        sm.new_session()
        assert sm.leaf_id is None

    def test_new_session_with_parent(self, tmp_path):
        sm = SessionManager.create(tmp_path, session_dir=tmp_path / "s")
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        prev_file = sm.session_file
        sm.new_session()
        header = sm.get_header()
        assert header is not None
