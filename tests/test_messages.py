"""Tests for message models (SystemMessage, HumanMessage, AIMessage, ToolMessage)."""

import pytest
from operator_use.messages.service import (
    Usage,
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)


# --- Usage ---


def test_usage_basic():
    u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    assert u.total_tokens == 30
    assert u.image_tokens is None


def test_usage_with_optional_fields():
    u = Usage(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        thinking_tokens=5,
        cache_read_input_tokens=3,
    )
    assert u.thinking_tokens == 5
    assert u.cache_read_input_tokens == 3


# --- SystemMessage ---


def test_system_message():
    msg = SystemMessage(content="You are a helpful assistant.")
    assert msg.role == "system"
    assert msg.content == "You are a helpful assistant."


def test_system_message_to_dict():
    msg = SystemMessage(content="prompt")
    d = msg.to_dict()
    assert d["role"] == "system"
    assert d["content"] == "prompt"


def test_system_message_repr():
    msg = SystemMessage(content="short")
    assert "SystemMessage" in repr(msg)


# --- HumanMessage ---


def test_human_message():
    msg = HumanMessage(content="Hello!")
    assert msg.role == "human"
    assert msg.content == "Hello!"
    assert msg.metadata == {}


def test_human_message_with_metadata():
    msg = HumanMessage(content="hi", metadata={"user_id": "abc"})
    d = msg.to_dict()
    assert d["metadata"]["user_id"] == "abc"


def test_human_message_empty_metadata_in_dict():
    msg = HumanMessage(content="hi")
    d = msg.to_dict()
    # metadata is always present in serialized form (empty dict when not set)
    assert "metadata" in d
    assert d["metadata"] == {}


# --- AIMessage ---


def test_ai_message_basic():
    msg = AIMessage(content="I can help.")
    assert msg.role == "ai"
    assert msg.thinking is None


def test_ai_message_with_thinking():
    msg = AIMessage(content="answer", thinking="reasoning here")
    assert msg.thinking == "reasoning here"


def test_ai_message_with_usage():
    usage = Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
    msg = AIMessage(content="ok", usage=usage)
    assert msg.usage.total_tokens == 15


def test_ai_message_to_dict():
    msg = AIMessage(content="hello")
    d = msg.to_dict()
    assert d["role"] == "ai"


# --- ToolMessage ---


def test_tool_message():
    msg = ToolMessage(id="t1", name="search", params={"query": "test"}, content="result")
    assert msg.role == "tool"
    assert msg.name == "search"
    assert msg.params == {"query": "test"}


def test_tool_message_to_dict():
    msg = ToolMessage(id="t1", name="run", params={}, content="done")
    d = msg.to_dict()
    assert d["role"] == "tool"
    assert d["id"] == "t1"


# --- BaseMessage.from_dict dispatch ---


def test_from_dict_system():
    msg = BaseMessage.from_dict({"role": "system", "content": "sys"})
    assert isinstance(msg, SystemMessage)


def test_from_dict_human():
    msg = BaseMessage.from_dict({"role": "human", "content": "hi"})
    assert isinstance(msg, HumanMessage)


def test_from_dict_ai():
    msg = BaseMessage.from_dict({"role": "ai", "content": "response"})
    assert isinstance(msg, AIMessage)


def test_from_dict_tool():
    msg = BaseMessage.from_dict({"role": "tool", "id": "t1", "name": "run", "content": "ok"})
    assert isinstance(msg, ToolMessage)


def test_from_dict_unknown_role_raises():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BaseMessage.from_dict({"role": "unknown", "content": "test"})


# --- Roundtrip serialization ---


def test_roundtrip_human():
    original = HumanMessage(content="hello", metadata={"x": 1})
    restored = BaseMessage.from_dict(original.to_dict())
    assert isinstance(restored, HumanMessage)
    assert restored.content == "hello"


def test_roundtrip_ai():
    original = AIMessage(content="answer", thinking="thought")
    restored = BaseMessage.from_dict(original.to_dict())
    assert isinstance(restored, AIMessage)
    assert restored.content == "answer"


def test_roundtrip_tool():
    original = ToolMessage(id="t1", name="fs", params={"path": "/tmp"}, content="listed")
    restored = BaseMessage.from_dict(original.to_dict())
    assert isinstance(restored, ToolMessage)
    assert restored.name == "fs"
