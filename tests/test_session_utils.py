"""Tests for session/utils.py: ID generation, session file I/O, session listing."""
import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from operator_use.session.utils import (
    create_session_id,
    generate_id,
    generate_timestamp,
    get_default_session_dir,
    read_session_file,
    is_valid_session_file,
    find_most_recent_session,
    is_message_with_contents,
    get_last_activity_time,
    list_sessions_from_dir,
)
from operator_use.session.types import SessionHeader, SessionType, MessageEntry
from operator_use.message.types import UserMessage, AssistantMessage, TextContent, Role
from operator_use.session.manager import SessionManager


# ── create_session_id ─────────────────────────────────────────────────────────

class TestCreateSessionId:
    def test_returns_string(self):
        assert isinstance(create_session_id(), str)

    def test_unique_each_call(self):
        ids = {create_session_id() for _ in range(20)}
        assert len(ids) == 20


# ── generate_id ───────────────────────────────────────────────────────────────

class TestGenerateId:
    def test_returns_8_chars(self):
        eid = generate_id(set())
        assert len(eid) == 8

    def test_avoids_existing_ids(self):
        existing = {"abcd1234", "efgh5678"}
        for _ in range(50):
            new_id = generate_id(existing)
            assert new_id not in existing

    def test_collision_fallback(self):
        # Fill a set with all possible 8-char IDs isn't feasible,
        # but we can verify it still returns something when given a large set
        existing = set()
        ids = [generate_id(existing) for _ in range(100)]
        assert all(len(i) >= 8 for i in ids)


# ── generate_timestamp ────────────────────────────────────────────────────────

class TestGenerateTimestamp:
    def test_returns_float(self):
        ts = generate_timestamp()
        assert isinstance(ts, float)

    def test_is_recent(self):
        ts = generate_timestamp()
        now = datetime.now().timestamp()
        assert abs(ts - now) < 2.0


# ── get_default_session_dir ───────────────────────────────────────────────────

class TestGetDefaultSessionDir:
    def test_creates_directory(self, tmp_path):
        result = get_default_session_dir("/some/project", agent_dir=tmp_path)
        assert result.is_dir()

    def test_encodes_cwd_in_path(self, tmp_path):
        result = get_default_session_dir("/my/project", agent_dir=tmp_path)
        assert "my" in str(result) or "project" in str(result)

    def test_different_cwds_produce_different_dirs(self, tmp_path):
        d1 = get_default_session_dir("/proj/a", agent_dir=tmp_path)
        d2 = get_default_session_dir("/proj/b", agent_dir=tmp_path)
        assert d1 != d2


# ── read_session_file / is_valid_session_file ─────────────────────────────────

def _write_session_file(path: Path) -> Path:
    """Write a valid JSONL session file (header + user + assistant entries)."""
    from operator_use.session.types import SessionHeader, MessageEntry
    from operator_use.session.utils import generate_id, generate_timestamp
    import time

    header = SessionHeader(id="test-session-id", timestamp=generate_timestamp(), cwd=str(path.parent))
    user_msg = UserMessage.text("hello")
    asst_msg = AssistantMessage()
    asst_msg.contents = [TextContent(content="hi")]

    user_entry = MessageEntry(id=generate_id(set()), parent_id=None, timestamp=generate_timestamp(), message=user_msg)
    asst_entry = MessageEntry(id=generate_id({user_entry.id}), parent_id=user_entry.id, timestamp=generate_timestamp(), message=asst_msg)

    lines = [
        header.model_dump_json(exclude_none=True),
        user_entry.model_dump_json(exclude_none=True),
        asst_entry.model_dump_json(exclude_none=True),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestReadSessionFile:
    def test_returns_empty_for_nonexistent(self, tmp_path):
        entries = read_session_file(tmp_path / "ghost.jsonl")
        assert entries == []

    def test_returns_entries_for_valid_file(self, tmp_path):
        path = tmp_path / "s.jsonl"
        _write_session_file(path)
        entries = read_session_file(path)
        assert len(entries) >= 1

    def test_first_entry_is_header(self, tmp_path):
        path = tmp_path / "s.jsonl"
        _write_session_file(path)
        entries = read_session_file(path)
        assert isinstance(entries[0], SessionHeader)

    def test_returns_empty_for_missing_header(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text('{"type":"message_entry","id":"x"}\n')
        entries = read_session_file(path)
        assert entries == []

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "s.jsonl"
        _write_session_file(path)
        with open(path, "a") as f:
            f.write("NOT_JSON\n")
        entries = read_session_file(path)
        assert len(entries) >= 1


class TestIsValidSessionFile:
    def test_valid_file_returns_true(self, tmp_path):
        path = tmp_path / "s.jsonl"
        _write_session_file(path)
        assert is_valid_session_file(path) is True

    def test_nonexistent_returns_false(self, tmp_path):
        assert is_valid_session_file(tmp_path / "ghost.jsonl") is False

    def test_empty_file_returns_false(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert is_valid_session_file(path) is False

    def test_invalid_json_returns_false(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n")
        assert is_valid_session_file(path) is False


# ── find_most_recent_session ──────────────────────────────────────────────────

class TestFindMostRecentSession:
    def test_returns_none_for_empty_dir(self, tmp_path):
        assert find_most_recent_session(tmp_path) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        assert find_most_recent_session(tmp_path / "missing") is None

    def test_returns_most_recent(self, tmp_path):
        p1 = tmp_path / "a.jsonl"
        p2 = tmp_path / "b.jsonl"
        _write_session_file(p1)
        import time; time.sleep(0.01)
        _write_session_file(p2)
        result = find_most_recent_session(tmp_path)
        assert result == p2


# ── is_message_with_contents ──────────────────────────────────────────────────

class TestIsMessageWithContents:
    def test_user_with_text_is_true(self):
        assert is_message_with_contents(UserMessage.text("hi")) is True

    def test_assistant_with_text_is_true(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="ok")]
        assert is_message_with_contents(msg) is True

    def test_empty_user_message_is_false(self):
        msg = UserMessage()
        msg.contents = []
        assert is_message_with_contents(msg) is False


# ── get_last_activity_time ────────────────────────────────────────────────────

class TestGetLastActivityTime:
    def test_returns_none_for_empty(self):
        assert get_last_activity_time([]) is None

    def test_returns_timestamp_for_messages(self):
        sm = SessionManager.in_memory()
        sm.append_message(UserMessage.text("hi"))
        entries = sm.get_entries()
        result = get_last_activity_time(entries)
        assert result is not None
        assert isinstance(result, float)


# ── list_sessions_from_dir ────────────────────────────────────────────────────

class TestListSessionsFromDir:
    def test_returns_empty_for_missing_dir(self, tmp_path):
        result = list_sessions_from_dir(tmp_path / "missing")
        assert result == []

    def test_lists_valid_sessions(self, tmp_path):
        path = tmp_path / "s.jsonl"
        _write_session_file(path)
        result = list_sessions_from_dir(tmp_path)
        assert len(result) == 1

    def test_skips_invalid_files(self, tmp_path):
        (tmp_path / "bad.jsonl").write_text("garbage\n")
        result = list_sessions_from_dir(tmp_path)
        assert result == []

    def test_progress_callback_called(self, tmp_path):
        _write_session_file(tmp_path / "s.jsonl")
        progress = []
        list_sessions_from_dir(tmp_path, on_progress=lambda done, total: progress.append((done, total)))
        assert len(progress) >= 1

    def test_multiple_sessions(self, tmp_path):
        _write_session_file(tmp_path / "a.jsonl")
        _write_session_file(tmp_path / "b.jsonl")
        result = list_sessions_from_dir(tmp_path)
        assert len(result) == 2
