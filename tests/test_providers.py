"""Tests for provider models and events."""

from operator_use.providers.views import TokenUsage, Metadata
from operator_use.providers.events import (
    LLMStreamEventType,
    LLMEventType,
    Thinking,
    ToolCall,
    LLMStreamEvent,
    LLMEvent,
)


# --- TokenUsage ---


def test_token_usage_basic():
    u = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 20
    assert u.total_tokens == 30


def test_token_usage_optional_none():
    u = TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    assert u.image_tokens is None
    assert u.thinking_tokens is None
    assert u.cache_creation_input_tokens is None
    assert u.cache_read_input_tokens is None


def test_token_usage_with_all_fields():
    u = TokenUsage(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        image_tokens=5,
        thinking_tokens=3,
        cache_creation_input_tokens=2,
        cache_read_input_tokens=1,
    )
    assert u.image_tokens == 5
    assert u.thinking_tokens == 3


# --- Metadata ---


def test_metadata():
    m = Metadata(name="claude-3", context_window=200000, owned_by="anthropic")
    assert m.name == "claude-3"
    assert m.context_window == 200000
    assert m.owned_by == "anthropic"


# --- LLMStreamEventType ---


def test_stream_event_type_values():
    assert LLMStreamEventType.TEXT_START == "text_start"
    assert LLMStreamEventType.TEXT_DELTA == "text_delta"
    assert LLMStreamEventType.TEXT_END == "text_end"
    assert LLMStreamEventType.TOOL_CALL == "tool_call"
    assert LLMStreamEventType.THINK_START == "think_start"
    assert LLMStreamEventType.THINK_DELTA == "think_delta"
    assert LLMStreamEventType.THINK_END == "think_end"


# --- LLMEventType ---


def test_event_type_values():
    assert LLMEventType.TEXT == "text"
    assert LLMEventType.TOOL_CALL == "tool_call"


# --- Thinking ---


def test_thinking_defaults():
    t = Thinking()
    assert t.content is None
    assert t.signature is None


def test_thinking_with_content():
    t = Thinking(content="let me reason...")
    assert t.content == "let me reason..."


def test_thinking_with_bytes_signature():
    t = Thinking(content="thought", signature=b"\x00\x01\x02")
    assert t.signature == b"\x00\x01\x02"


# --- ToolCall ---


def test_tool_call():
    tc = ToolCall(id="tc1", name="search", params={"query": "test"})
    assert tc.id == "tc1"
    assert tc.name == "search"
    assert tc.params == {"query": "test"}


def test_tool_call_empty_params():
    tc = ToolCall(id="tc2", name="noop", params={})
    assert tc.params == {}


# --- LLMStreamEvent ---


def test_llm_stream_event_text():
    e = LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content="hello")
    assert e.type == LLMStreamEventType.TEXT_DELTA
    assert e.content == "hello"
    assert e.tool_call is None


def test_llm_stream_event_tool_call():
    tc = ToolCall(id="t1", name="run", params={})
    e = LLMStreamEvent(type=LLMStreamEventType.TOOL_CALL, tool_call=tc)
    assert e.tool_call.name == "run"


def test_llm_stream_event_with_usage():
    usage = TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
    e = LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)
    assert e.usage.total_tokens == 15


# --- LLMEvent ---


def test_llm_event_text():
    e = LLMEvent(type=LLMEventType.TEXT, content="response")
    assert e.content == "response"
    assert e.tool_call is None


def test_llm_event_tool_call():
    tc = ToolCall(id="t1", name="fs", params={"path": "/tmp"})
    e = LLMEvent(type=LLMEventType.TOOL_CALL, tool_call=tc)
    assert e.tool_call.params == {"path": "/tmp"}
