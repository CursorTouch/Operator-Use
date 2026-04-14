"""Tests for Orchestrator — routing, message building, outgoing building."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_use.orchestrator.service import Orchestrator, _extract_file_content
from operator_use.bus.service import Bus
from operator_use.bus.views import IncomingMessage, TextPart, AudioPart, FilePart
from operator_use.messages.service import AIMessage, HumanMessage
from operator_use.bus.views import StreamPhase


# --- _extract_file_content (pure function) ---


def test_extract_file_not_found(tmp_path):
    result = _extract_file_content(str(tmp_path / "ghost.txt"))
    assert "not found" in result


def test_extract_inline_text_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello world", encoding="utf-8")
    result = _extract_file_content(str(f))
    assert "hello world" in result
    assert "note.txt" in result


def test_extract_inline_python_file(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('hi')", encoding="utf-8")
    result = _extract_file_content(str(f))
    assert "print" in result


def test_extract_large_text_file_not_inlined(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 9000, encoding="utf-8")
    result = _extract_file_content(str(f))
    assert "too large" in result
    assert "file reading tools" in result


def test_extract_binary_file_gives_path(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")
    result = _extract_file_content(str(f))
    assert "PDF" in result
    assert str(f) in result


def test_extract_unknown_binary_extension(tmp_path):
    f = tmp_path / "archive.zip"
    f.write_bytes(b"PK fake zip")
    result = _extract_file_content(str(f))
    assert "ZIP archive" in result


# --- Mock Agent helper ---


def make_mock_agent(response_text="mock response"):
    agent = MagicMock()
    agent.llm = MagicMock()
    agent.llm.astream = None
    response = AIMessage(content=response_text)
    agent.run = AsyncMock(return_value=response)
    return agent


# --- Orchestrator._user_sent_voice ---


def test_user_sent_voice_true():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[AudioPart(audio="/v.ogg")])
    assert orch._user_sent_voice(msg) is True


def test_user_sent_voice_false():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="hi")])
    assert orch._user_sent_voice(msg) is False


def test_user_sent_voice_empty_parts():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[])
    assert orch._user_sent_voice(msg) is False


# --- Orchestrator._resolve_agent ---


def test_resolve_agent_default():
    bus = Bus()
    agent = make_mock_agent()
    orch = Orchestrator(bus=bus, agents={"operator": agent}, default_agent="operator")
    msg = IncomingMessage(channel="telegram", chat_id="1")
    resolved = orch._resolve_agent(msg)
    assert resolved is agent


def test_resolve_agent_custom_router():
    bus = Bus()
    a1 = make_mock_agent("a1")
    a2 = make_mock_agent("a2")
    orch = Orchestrator(
        bus=bus,
        agents={"agent1": a1, "agent2": a2},
        router=lambda msg: "agent2",
        default_agent="agent1",
    )
    msg = IncomingMessage(channel="telegram", chat_id="1")
    resolved = orch._resolve_agent(msg)
    assert resolved is a2


def test_resolve_agent_fallback_to_default():
    bus = Bus()
    agent = make_mock_agent()
    orch = Orchestrator(
        bus=bus,
        agents={"operator": agent},
        router=lambda msg: "nonexistent",
        default_agent="operator",
    )
    msg = IncomingMessage(channel="telegram", chat_id="1")
    resolved = orch._resolve_agent(msg)
    assert resolved is agent


def test_resolve_agent_no_match_raises():
    bus = Bus()
    orch = Orchestrator(
        bus=bus,
        agents={},
        default_agent="operator",
    )
    msg = IncomingMessage(channel="telegram", chat_id="1")
    with pytest.raises(ValueError, match="No agent found"):
        orch._resolve_agent(msg)


# --- Orchestrator._build_request_message ---


@pytest.mark.asyncio
async def test_build_request_text_message():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="hello")])
    built = await orch._build_request_message(msg)
    assert isinstance(built, HumanMessage)
    assert built.content == "hello"


@pytest.mark.asyncio
async def test_build_request_multi_text_parts():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(
        channel="telegram",
        chat_id="1",
        parts=[TextPart(content="line 1"), TextPart(content="line 2")],
    )
    built = await orch._build_request_message(msg)
    assert "line 1" in built.content
    assert "line 2" in built.content


@pytest.mark.asyncio
async def test_build_request_empty_parts():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[])
    built = await orch._build_request_message(msg)
    assert "[empty message]" in built.content


@pytest.mark.asyncio
async def test_build_request_file_part(tmp_path):
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    f = tmp_path / "data.txt"
    f.write_text("file content", encoding="utf-8")
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[FilePart(path=str(f))])
    built = await orch._build_request_message(msg)
    assert "file content" in built.content


@pytest.mark.asyncio
async def test_build_request_audio_no_stt():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()}, stt=None)
    msg = IncomingMessage(
        channel="telegram", chat_id="1", parts=[AudioPart(audio="transcribed text")]
    )
    built = await orch._build_request_message(msg)
    assert "transcribed text" in built.content


# --- Orchestrator._build_outgoing_message ---


@pytest.mark.asyncio
async def test_build_outgoing_text():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    incoming = IncomingMessage(channel="telegram", chat_id="42", parts=[TextPart(content="hi")])
    response = AIMessage(content="Hello!")
    outgoing = await orch._build_outgoing_message(incoming, response, streamed=False)
    assert outgoing.channel == "telegram"
    assert outgoing.chat_id == "42"
    assert outgoing.reply is True
    assert any(p.content == "Hello!" for p in outgoing.parts if hasattr(p, "content"))


@pytest.mark.asyncio
async def test_build_outgoing_strips_msg_id():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    incoming = IncomingMessage(channel="discord", chat_id="1", parts=[TextPart(content="q")])
    response = AIMessage(content="[msg_id:123] Clean response")
    outgoing = await orch._build_outgoing_message(incoming, response, streamed=False)
    text = next(p.content for p in outgoing.parts if hasattr(p, "content"))
    assert "[msg_id:123]" not in text
    assert "Clean response" in text


@pytest.mark.asyncio
async def test_build_outgoing_streamed_sets_done_phase():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    incoming = IncomingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="q")])
    response = AIMessage(content="done")
    outgoing = await orch._build_outgoing_message(incoming, response, streamed=True)
    assert outgoing.stream_phase == StreamPhase.DONE


@pytest.mark.asyncio
async def test_build_outgoing_not_streamed_no_phase():
    bus = Bus()
    orch = Orchestrator(bus=bus, agents={"operator": make_mock_agent()})
    incoming = IncomingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="q")])
    response = AIMessage(content="response")
    outgoing = await orch._build_outgoing_message(incoming, response, streamed=False)
    assert outgoing.stream_phase is None
