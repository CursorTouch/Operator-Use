"""Tests for bus message models and helper functions."""

from operator_use.bus.views import (
    TextPart,
    ImagePart,
    AudioPart,
    FilePart,
    StreamPhase,
    IncomingMessage,
    OutgoingMessage,
    text_from_parts,
    media_paths_from_parts,
)


# --- Part models ---


def test_text_part():
    p = TextPart(content="hello")
    assert p.content == "hello"


def test_image_part_defaults():
    p = ImagePart()
    assert p.images == []
    assert p.paths is None
    assert p.mime_type is None


def test_image_part_with_paths():
    p = ImagePart(paths=["/img/a.png", "/img/b.png"], mime_type="image/png")
    assert len(p.paths) == 2


def test_audio_part():
    p = AudioPart(audio="/audio/voice.ogg")
    assert p.audio == "/audio/voice.ogg"


def test_file_part():
    p = FilePart(path="/docs/report.pdf")
    assert p.path == "/docs/report.pdf"


# --- text_from_parts ---


def test_text_from_parts_only_text():
    parts = [TextPart(content="hello"), TextPart(content="world")]
    assert text_from_parts(parts) == "hello\nworld"


def test_text_from_parts_mixed():
    parts = [TextPart(content="hi"), AudioPart(audio="/a.ogg"), FilePart(path="/f.pdf")]
    assert text_from_parts(parts) == "hi"


def test_text_from_parts_no_text():
    parts = [AudioPart(audio="/a.ogg"), FilePart(path="/f.pdf")]
    assert text_from_parts(parts) == ""


def test_text_from_parts_empty():
    assert text_from_parts([]) == ""


# --- media_paths_from_parts ---


def test_media_paths_from_audio():
    parts = [AudioPart(audio="/voice.ogg")]
    assert media_paths_from_parts(parts) == ["/voice.ogg"]


def test_media_paths_from_file():
    parts = [FilePart(path="/doc.pdf")]
    assert media_paths_from_parts(parts) == ["/doc.pdf"]


def test_media_paths_from_image_with_paths():
    parts = [ImagePart(paths=["/img1.png", "/img2.png"])]
    assert media_paths_from_parts(parts) == ["/img1.png", "/img2.png"]


def test_media_paths_image_no_paths():
    parts = [ImagePart(images=["base64data"])]
    assert media_paths_from_parts(parts) == []


def test_media_paths_mixed():
    parts = [
        TextPart(content="text"),
        AudioPart(audio="/a.ogg"),
        FilePart(path="/b.pdf"),
        ImagePart(paths=["/c.png"]),
    ]
    assert media_paths_from_parts(parts) == ["/a.ogg", "/b.pdf", "/c.png"]


def test_media_paths_empty():
    assert media_paths_from_parts([]) == []


# --- StreamPhase ---


def test_stream_phase_values():
    assert StreamPhase.START == "start"
    assert StreamPhase.CHUNK == "chunk"
    assert StreamPhase.END == "end"
    assert StreamPhase.DONE == "done"


# --- Message models ---


def test_incoming_message():
    msg = IncomingMessage(
        channel="telegram",
        chat_id="123",
        parts=[TextPart(content="hi")],
        user_id="user1",
    )
    assert msg.channel == "telegram"
    assert msg.user_id == "user1"
    assert msg.account_id == ""


def test_outgoing_message_defaults():
    msg = OutgoingMessage(channel="discord", chat_id="456")
    assert msg.reply is False
    assert msg.stream_phase is None
    assert msg.continue_typing is False


def test_outgoing_message_with_stream_phase():
    msg = OutgoingMessage(channel="discord", chat_id="456", stream_phase=StreamPhase.START)
    assert msg.stream_phase == StreamPhase.START
