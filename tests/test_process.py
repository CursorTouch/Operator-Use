"""Tests for ProcessSession and ProcessStore."""

import asyncio
import pytest
from unittest.mock import MagicMock

from operator_use.process.views import ProcessSession
from operator_use.process.service import ProcessStore


# --- ProcessSession (view) ---


def make_mock_session(running: bool = True) -> ProcessSession:
    proc = MagicMock()
    proc.returncode = None if running else 0
    return ProcessSession(session_id="abc123", cmd="echo hi", process=proc)


def test_session_is_running_true():
    s = make_mock_session(running=True)
    assert s.is_running is True


def test_session_is_running_false():
    s = make_mock_session(running=False)
    assert s.is_running is False


def test_session_exit_code_none_when_running():
    s = make_mock_session(running=True)
    assert s.exit_code is None


def test_session_exit_code_when_done():
    proc = MagicMock()
    proc.returncode = 1
    s = ProcessSession(session_id="x", cmd="fail", process=proc)
    assert s.exit_code == 1


def test_session_tail_returns_last_n():
    s = make_mock_session()
    s.output = [f"line{i}" for i in range(10)]
    result = s.tail(3)
    assert result == "line7\nline8\nline9"


def test_session_tail_fewer_lines_than_n():
    s = make_mock_session()
    s.output = ["only", "two"]
    result = s.tail(10)
    assert result == "only\ntwo"


def test_session_tail_empty_output():
    s = make_mock_session()
    assert s.tail(5) == ""


def test_session_full_log():
    s = make_mock_session()
    s.output = ["line1", "line2", "line3"]
    assert s.full_log() == "line1\nline2\nline3"


def test_session_full_log_empty():
    s = make_mock_session()
    assert s.full_log() == ""


def test_session_has_started_at():
    s = make_mock_session()
    assert s.started_at is not None


# --- ProcessStore ---


def test_process_store_get_missing():
    store = ProcessStore()
    assert store.get("nonexistent") is None


def test_process_store_clear_nonexistent():
    store = ProcessStore()
    assert store.clear("ghost") is False


def test_process_store_clear_running_session():
    store = ProcessStore()
    proc = MagicMock()
    proc.returncode = None
    session = ProcessSession(session_id="s1", cmd="sleep 10", process=proc)
    session._reader = None
    store._sessions["s1"] = session
    result = store.clear("s1")
    assert result is True
    assert store.get("s1") is None
    proc.terminate.assert_called_once()


def test_process_store_clear_finished_session():
    store = ProcessStore()
    proc = MagicMock()
    proc.returncode = 0
    session = ProcessSession(session_id="s2", cmd="echo done", process=proc)
    session._reader = None
    store._sessions["s2"] = session
    result = store.clear("s2")
    assert result is True
    proc.terminate.assert_not_called()


def test_process_store_clear_cancels_reader():
    store = ProcessStore()
    proc = MagicMock()
    proc.returncode = None
    session = ProcessSession(session_id="s3", cmd="tail -f log", process=proc)
    reader = MagicMock()
    reader.done.return_value = False
    session._reader = reader
    store._sessions["s3"] = session
    store.clear("s3")
    reader.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_process_store_spawn_and_get():
    store = ProcessStore()
    session = await store.spawn("echo hello")
    assert session is not None
    assert session.session_id is not None
    found = store.get(session.session_id)
    assert found is session
    # cleanup
    store.clear(session.session_id)


@pytest.mark.asyncio
async def test_process_store_spawn_output_captured():
    store = ProcessStore()
    session = await store.spawn("echo captured_output")
    await asyncio.sleep(0.3)
    log = session.full_log()
    store.clear(session.session_id)
    assert "captured_output" in log
