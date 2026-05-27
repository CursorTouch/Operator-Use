"""Verify the ChannelEntry + MessageMeta wiring end-to-end."""

from pathlib import Path

from operator_use.session.manager import SessionManager
from operator_use.session.types import (
    ChannelEntry, MessageEntry, MessageMeta, MessageAttachment,
)
from operator_use.message.types import UserMessage, TextContent
from operator_use.agent.types import PromptOptions


def test_append_channel_entry_writes_entry(tmp_path: Path) -> None:
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, persist=False)
    sm.append_channel_entry("telegram", chat_id="123", user_id="456")
    channel_entries = [e for e in sm.entries if isinstance(e, ChannelEntry)]
    assert len(channel_entries) == 1
    assert channel_entries[0].name == "telegram"
    assert channel_entries[0].chat_id == "123"
    assert channel_entries[0].user_id == "456"


def test_get_current_channel_returns_latest(tmp_path: Path) -> None:
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, persist=False)
    assert sm.get_current_channel() is None
    sm.append_channel_entry("telegram", "1", "u1")
    assert sm.get_current_channel() == "telegram"
    sm.append_channel_entry("discord", "2", "u2")
    assert sm.get_current_channel() == "discord"


def test_append_message_with_meta(tmp_path: Path) -> None:
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, persist=False)
    meta = MessageMeta(
        reply_to="789",
        attachments=[MessageAttachment(path="/tmp/voice.ogg", mime_type="audio/ogg")],
    )
    msg = UserMessage(contents=[TextContent(content="hello")])
    sm.append_message(msg, meta=meta)

    message_entries = [e for e in sm.entries if isinstance(e, MessageEntry)]
    assert len(message_entries) == 1
    assert message_entries[0].meta is not None
    assert message_entries[0].meta.reply_to == "789"
    assert message_entries[0].meta.attachments is not None
    assert message_entries[0].meta.attachments[0].path == "/tmp/voice.ogg"
    assert message_entries[0].meta.attachments[0].mime_type == "audio/ogg"


def test_append_message_without_meta_defaults_none(tmp_path: Path) -> None:
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, persist=False)
    msg = UserMessage(contents=[TextContent(content="hi")])
    sm.append_message(msg)
    message_entries = [e for e in sm.entries if isinstance(e, MessageEntry)]
    assert message_entries[0].meta is None


def test_channel_entry_persisted_to_jsonl(tmp_path: Path) -> None:
    session_file = tmp_path / "session.jsonl"
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, session_file=session_file, persist=True)
    # Need an assistant message to trigger the deferred flush
    from operator_use.message.types import AssistantMessage
    sm.append_channel_entry("telegram", "1", "u1")
    sm.append_message(UserMessage(contents=[TextContent(content="hi")]))
    sm.append_message(AssistantMessage(contents=[TextContent(content="hello")]))
    assert session_file.exists()

    # Reload and confirm entries survive a round-trip
    sm2 = SessionManager(cwd=tmp_path, session_dir=tmp_path, session_file=session_file, persist=True)
    channel_entries = [e for e in sm2.entries if isinstance(e, ChannelEntry)]
    assert len(channel_entries) == 1
    assert channel_entries[0].name == "telegram"


def test_message_meta_round_trips_through_jsonl(tmp_path: Path) -> None:
    session_file = tmp_path / "session.jsonl"
    sm = SessionManager(cwd=tmp_path, session_dir=tmp_path, session_file=session_file, persist=True)
    from operator_use.message.types import AssistantMessage
    meta = MessageMeta(
        reply_to="t_42",
        attachments=[MessageAttachment(path="/a.png", mime_type="image/png")],
    )
    sm.append_message(UserMessage(contents=[TextContent(content="q")]), meta=meta)
    sm.append_message(AssistantMessage(contents=[TextContent(content="a")]))

    sm2 = SessionManager(cwd=tmp_path, session_dir=tmp_path, session_file=session_file, persist=True)
    # Note: AgentMessage is an undiscriminated Union in this project, so role
    # doesn't survive reload. Check by structural position instead — the first
    # MessageEntry is the user message we appended.
    message_entries = [e for e in sm2.entries if isinstance(e, MessageEntry)]
    assert len(message_entries) == 2
    first = message_entries[0]
    assert first.meta is not None
    assert first.meta.reply_to == "t_42"
    assert first.meta.attachments is not None
    assert first.meta.attachments[0].path == "/a.png"


def test_prompt_options_carries_meta() -> None:
    meta = MessageMeta(reply_to="9")
    opts = PromptOptions(source="interactive", meta=meta)
    assert opts.meta is meta
    assert opts.meta.reply_to == "9"


def test_prompt_options_meta_defaults_none() -> None:
    opts = PromptOptions()
    assert opts.meta is None
