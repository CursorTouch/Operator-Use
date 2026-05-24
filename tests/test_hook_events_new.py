"""Tests for newly added hook event types: SubagentStart/End and bus message types."""
from __future__ import annotations

import pytest

from program.hooks.types import (
    ChannelConnectEvent,
    ChannelDisconnectEvent,
    GatewayErrorEvent,
    MessageCancelEvent,
    MessageReceiveEvent,
    MessageSendEvent,
    SubagentStartEvent,
    SubagentEndEvent,
    HookEvent,
)
from program.subagent.types import SubagentStatus
from program.bus.types import (
    IncomingMessage, OutgoingMessage, TextPart, AudioPart, ImagePart, FilePart,
    ContentPart, StreamPhase, text_from_parts, media_paths_from_parts,
)


# ── SubagentStartEvent ────────────────────────────────────────────────────────

class TestSubagentStartEvent:
    def test_type_field_fixed(self):
        e = SubagentStartEvent()
        assert e.type == 'subagent_start'

    def test_type_not_overrideable(self):
        e = SubagentStartEvent(task_id='t1', label='lbl', task='do thing')
        assert e.type == 'subagent_start'

    def test_fields(self):
        e = SubagentStartEvent(task_id='abc', label='fetch', task='Fetch data')
        assert e.task_id == 'abc'
        assert e.label == 'fetch'
        assert e.task == 'Fetch data'

    def test_defaults(self):
        e = SubagentStartEvent()
        assert e.task_id == ''
        assert e.label == ''
        assert e.task == ''

    def test_is_hook_event(self):
        # SubagentStartEvent must be part of the HookEvent union
        e = SubagentStartEvent()
        # isinstance check won't work on union, but we can verify type annotation via
        # checking the union members
        from program.hooks.types import HookEvent
        import typing
        args = typing.get_args(HookEvent)
        assert SubagentStartEvent in args


# ── SubagentEndEvent ──────────────────────────────────────────────────────────

class TestSubagentEndEvent:
    def test_type_field_fixed(self):
        e = SubagentEndEvent()
        assert e.type == 'subagent_end'

    def test_default_status_completed(self):
        e = SubagentEndEvent()
        assert e.status == SubagentStatus.completed

    def test_failed_status(self):
        e = SubagentEndEvent(status=SubagentStatus.failed)
        assert e.status == SubagentStatus.failed

    def test_result_none_by_default(self):
        e = SubagentEndEvent()
        assert e.result is None

    def test_result_can_be_set(self):
        e = SubagentEndEvent(result="Here is the output")
        assert e.result == "Here is the output"

    def test_is_hook_event(self):
        import typing
        from program.hooks.types import HookEvent
        args = typing.get_args(HookEvent)
        assert SubagentEndEvent in args


# ── Gateway hook events ───────────────────────────────────────────────────────

class TestGatewayHookEvents:
    def test_gateway_transport_events_are_hook_events(self):
        import typing

        args = typing.get_args(HookEvent)
        assert ChannelConnectEvent in args
        assert ChannelDisconnectEvent in args
        assert MessageReceiveEvent in args
        assert MessageSendEvent in args
        assert MessageCancelEvent in args
        assert GatewayErrorEvent in args


# ── Bus ContentPart types ─────────────────────────────────────────────────────

class TestTextPart:
    def test_content_field(self):
        p = TextPart(content="hello")
        assert p.content == "hello"


class TestAudioPart:
    def test_required_field(self):
        p = AudioPart(audio="/tmp/voice.ogg")
        assert p.audio == "/tmp/voice.ogg"

    def test_optional_mime(self):
        p = AudioPart(audio="x.mp3", mime_type="audio/mpeg")
        assert p.mime_type == "audio/mpeg"

    def test_default_mime_none(self):
        p = AudioPart(audio="x.wav")
        assert p.mime_type is None


class TestImagePart:
    def test_default_empty_images(self):
        p = ImagePart()
        assert p.images == []

    def test_with_paths(self):
        p = ImagePart(paths=["/tmp/img.png"])
        assert p.paths == ["/tmp/img.png"]


class TestFilePart:
    def test_path_field(self):
        p = FilePart(path="/tmp/doc.pdf")
        assert p.path == "/tmp/doc.pdf"


# ── text_from_parts / media_paths_from_parts ──────────────────────────────────

class TestTextFromParts:
    def test_single_text_part(self):
        assert text_from_parts([TextPart("hi")]) == "hi"

    def test_multiple_text_parts_joined(self):
        result = text_from_parts([TextPart("hello"), TextPart("world")])
        assert result == "hello\nworld"

    def test_non_text_parts_ignored(self):
        parts = [AudioPart(audio="x"), TextPart("text")]
        assert text_from_parts(parts) == "text"

    def test_empty_list(self):
        assert text_from_parts([]) == ""


class TestMediaPathsFromParts:
    def test_audio_path_included(self):
        parts = [AudioPart(audio="/tmp/a.ogg")]
        assert "/tmp/a.ogg" in media_paths_from_parts(parts)

    def test_file_path_included(self):
        parts = [FilePart(path="/tmp/doc.pdf")]
        assert "/tmp/doc.pdf" in media_paths_from_parts(parts)

    def test_image_paths_included(self):
        parts = [ImagePart(paths=["/tmp/img.png", "/tmp/img2.png"])]
        paths = media_paths_from_parts(parts)
        assert "/tmp/img.png" in paths
        assert "/tmp/img2.png" in paths

    def test_text_parts_excluded(self):
        parts = [TextPart("just text")]
        assert media_paths_from_parts(parts) == []

    def test_image_without_paths(self):
        parts = [ImagePart()]  # no paths
        assert media_paths_from_parts(parts) == []


# ── StreamPhase ───────────────────────────────────────────────────────────────

class TestStreamPhase:
    def test_all_values(self):
        values = {p.value for p in StreamPhase}
        assert values == {'start', 'chunk', 'end', 'done', 'error'}


# ── IncomingMessage / OutgoingMessage ─────────────────────────────────────────

class TestIncomingMessageCreation:
    def test_minimal_creation(self):
        msg = IncomingMessage(channel="telegram", chat_id="42")
        assert msg.channel == "telegram"
        assert msg.chat_id == "42"
        assert msg.parts == []
        assert msg.user_id == ""
        assert msg.metadata == {}

    def test_with_text_part(self):
        msg = IncomingMessage(channel="slack", chat_id="C1", parts=[TextPart("hello")])
        assert text_from_parts(msg.parts) == "hello"

    def test_timestamp_set(self):
        msg = IncomingMessage(channel="stdio", chat_id="1")
        assert msg.timestamp is not None


class TestOutgoingMessageCreation:
    def test_minimal_creation(self):
        msg = OutgoingMessage(channel="discord", chat_id="789")
        assert msg.channel == "discord"
        assert msg.stream_phase is None

    def test_with_stream_phase(self):
        msg = OutgoingMessage(channel="ws:abc", chat_id="1", stream_phase=StreamPhase.CHUNK)
        assert msg.stream_phase == StreamPhase.CHUNK
