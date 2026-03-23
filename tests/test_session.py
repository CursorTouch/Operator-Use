"""Tests for Session and SessionStore."""

import pytest
from pathlib import Path
from datetime import datetime

from operator_use.session.views import Session
from operator_use.session.service import SessionStore
from operator_use.messages.service import HumanMessage, AIMessage


# --- Session ---

def test_session_defaults():
    s = Session(id="test-id")
    assert s.id == "test-id"
    assert s.messages == []
    assert s.metadata == {}
    assert isinstance(s.created_at, datetime)


def test_session_add_message():
    s = Session(id="s1")
    old_updated = s.updated_at
    msg = HumanMessage(content="hello")
    s.add_message(msg)
    assert len(s.messages) == 1
    assert s.updated_at >= old_updated


def test_session_get_history_returns_copy():
    s = Session(id="s1")
    s.add_message(HumanMessage(content="hi"))
    history = s.get_history()
    history.append(HumanMessage(content="extra"))
    assert len(s.messages) == 1  # original not modified


def test_session_clear():
    s = Session(id="s1")
    s.add_message(HumanMessage(content="hi"))
    s.add_message(AIMessage(content="hello"))
    s.clear()
    assert s.messages == []


def test_session_message_order():
    s = Session(id="s1")
    s.add_message(HumanMessage(content="first"))
    s.add_message(AIMessage(content="second"))
    history = s.get_history()
    assert history[0].content == "first"
    assert history[1].content == "second"


# --- SessionStore ---

def test_session_id_sanitization(tmp_path):
    store = SessionStore(tmp_path)
    safe = store._session_id_to_filename("telegram:123:456")
    assert ":" not in safe
    assert safe == "telegram_123_456"


def test_sessions_path(tmp_path):
    store = SessionStore(tmp_path)
    path = store._sessions_path("user:123")
    assert path.suffix == ".jsonl"
    assert ":" not in path.name


def test_get_or_create_new(tmp_path):
    store = SessionStore(tmp_path)
    session = store.get_or_create("my-session")
    assert session.id == "my-session"
    assert session.messages == []


def test_get_or_create_returns_same_instance(tmp_path):
    store = SessionStore(tmp_path)
    s1 = store.get_or_create("abc")
    s2 = store.get_or_create("abc")
    assert s1 is s2


def test_get_or_create_generates_id_when_none(tmp_path):
    store = SessionStore(tmp_path)
    session = store.get_or_create(None)
    assert session.id is not None
    assert len(session.id) > 0


def test_save_and_load_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    session = store.get_or_create("roundtrip")
    session.add_message(HumanMessage(content="hello"))
    session.add_message(AIMessage(content="world"))
    store.save(session)

    store2 = SessionStore(tmp_path)
    loaded = store2.load("roundtrip")
    assert loaded is not None
    assert loaded.id == "roundtrip"
    assert len(loaded.messages) == 2
    assert loaded.messages[0].content == "hello"
    assert loaded.messages[1].content == "world"


def test_load_nonexistent_returns_none(tmp_path):
    store = SessionStore(tmp_path)
    result = store.load("nonexistent-session")
    assert result is None


def test_delete_removes_session(tmp_path):
    store = SessionStore(tmp_path)
    session = store.get_or_create("del-me")
    store.save(session)
    result = store.delete("del-me")
    assert result is True
    assert store.load("del-me") is None


def test_delete_nonexistent_returns_false(tmp_path):
    store = SessionStore(tmp_path)
    assert store.delete("ghost") is False


def test_list_sessions(tmp_path):
    store = SessionStore(tmp_path)
    for sid in ["s1", "s2", "s3"]:
        s = store.get_or_create(sid)
        store.save(s)
    sessions = store.list_sessions()
    ids = {s["id"] for s in sessions}
    assert {"s1", "s2", "s3"}.issubset(ids)


def test_get_or_create_loads_from_disk(tmp_path):
    store1 = SessionStore(tmp_path)
    session = store1.get_or_create("persist")
    session.add_message(HumanMessage(content="persisted"))
    store1.save(session)

    store2 = SessionStore(tmp_path)
    loaded = store2.get_or_create("persist")
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "persisted"
