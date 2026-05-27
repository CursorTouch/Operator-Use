"""Tests for settings/storage.py: InMemorySettingsStorage and FileSettingsStorage."""
import json
import pytest
from pathlib import Path

from operator_use.settings.storage import InMemorySettingsStorage, FileSettingsStorage
from operator_use.settings.types import LockResult


# ── InMemorySettingsStorage ───────────────────────────────────────────────────

class TestInMemorySettingsStorage:
    def test_initial_global_data_is_empty_json(self):
        s = InMemorySettingsStorage()
        assert s.global_data == "{}"

    def test_initial_project_data_is_empty_json(self):
        s = InMemorySettingsStorage()
        assert s.project_data == "{}"

    def test_read_returns_current_global(self):
        s = InMemorySettingsStorage()
        result = s.with_lock("global", lambda current: LockResult(result=current, next=None))
        assert result.result == "{}"

    def test_read_returns_current_project(self):
        s = InMemorySettingsStorage()
        result = s.with_lock("project", lambda current: LockResult(result=current, next=None))
        assert result.result == "{}"

    def test_write_updates_global_data(self):
        s = InMemorySettingsStorage()
        s.with_lock("global", lambda _: LockResult(result=None, next='{"key": "val"}'))
        assert json.loads(s.global_data)["key"] == "val"

    def test_write_updates_project_data(self):
        s = InMemorySettingsStorage()
        s.with_lock("project", lambda _: LockResult(result=None, next='{"p": 1}'))
        assert json.loads(s.project_data)["p"] == 1

    def test_null_next_does_not_overwrite(self):
        s = InMemorySettingsStorage()
        s.with_lock("global", lambda _: LockResult(result=None, next='{"written": true}'))
        s.with_lock("global", lambda _: LockResult(result=None, next=None))
        assert json.loads(s.global_data)["written"] is True

    def test_global_and_project_independent(self):
        s = InMemorySettingsStorage()
        s.with_lock("global", lambda _: LockResult(result=None, next='{"scope": "global"}'))
        s.with_lock("project", lambda _: LockResult(result=None, next='{"scope": "project"}'))
        assert json.loads(s.global_data)["scope"] == "global"
        assert json.loads(s.project_data)["scope"] == "project"

    def test_fn_receives_latest_data(self):
        s = InMemorySettingsStorage()
        s.with_lock("global", lambda _: LockResult(result=None, next='{"v": 1}'))
        received = []
        s.with_lock("global", lambda current: (received.append(current), LockResult(result=None, next=None))[1])
        assert json.loads(received[0])["v"] == 1

    def test_result_returned_to_caller(self):
        s = InMemorySettingsStorage()
        result = s.with_lock("global", lambda _: LockResult(result="parsed-value", next=None))
        assert result.result == "parsed-value"


# ── FileSettingsStorage ───────────────────────────────────────────────────────

class TestFileSettingsStorage:
    def test_creates_parent_directory(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        s = FileSettingsStorage(cwd, agent_dir)
        assert agent_dir.is_dir()

    def test_read_returns_empty_object_when_no_file(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        s = FileSettingsStorage(cwd, agent_dir)
        result = s.with_lock("global", lambda current: LockResult(result=json.loads(current or "{}"), next=None))
        assert result.result == {}

    def test_write_creates_file(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        s = FileSettingsStorage(cwd, agent_dir)
        s.with_lock("global", lambda _: LockResult(result=None, next='{"key": "written"}'))
        assert s.global_settings_path.exists()
        assert json.loads(s.global_settings_path.read_text())["key"] == "written"

    def test_read_after_write_returns_written_data(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        s = FileSettingsStorage(cwd, agent_dir)
        s.with_lock("global", lambda _: LockResult(result=None, next='{"x": 42}'))
        result = s.with_lock("global", lambda current: LockResult(result=json.loads(current), next=None))
        assert result.result["x"] == 42

    def test_project_file_separate_from_global(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        s = FileSettingsStorage(cwd, agent_dir)
        s.with_lock("global", lambda _: LockResult(result=None, next='{"scope": "global"}'))
        s.with_lock("project", lambda _: LockResult(result=None, next='{"scope": "project"}'))
        assert s.global_settings_path != s.project_settings_path

    def test_null_next_does_not_write(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        s = FileSettingsStorage(cwd, agent_dir)
        s.with_lock("global", lambda _: LockResult(result=None, next='{"initial": true}'))
        s.with_lock("global", lambda _: LockResult(result=None, next=None))
        data = json.loads(s.global_settings_path.read_text())
        assert data.get("initial") is True
