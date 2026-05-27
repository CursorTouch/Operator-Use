"""Tests for Bus: queue size, concurrent producers/consumers, and message fields."""
import asyncio
import pytest
from operator_use.bus.service import Bus
from operator_use.bus.types import IncomingMessage, OutgoingMessage, TextPart, AudioPart, text_from_parts


def make_incoming(text: str, channel: str = "stdio", chat_id: str = "1") -> IncomingMessage:
    return IncomingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


def make_outgoing(text: str, channel: str = "stdio", chat_id: str = "1") -> OutgoingMessage:
    return OutgoingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


class TestIncomingMessageFields:
    def test_required_fields(self):
        msg = make_incoming("hi", channel="slack", chat_id="C1")
        assert msg.channel == "slack"
        assert msg.chat_id == "C1"
        assert text_from_parts(msg.parts) == "hi"

    def test_optional_fields_have_defaults(self):
        msg = make_incoming("")
        assert msg.user_id == ""
        assert msg.metadata == {}
        assert msg.timestamp is not None

    def test_audio_part(self):
        msg = IncomingMessage(
            channel="telegram", chat_id="42",
            parts=[AudioPart(audio="/tmp/voice.ogg", mime_type="audio/ogg")]
        )
        assert msg.parts[0].mime_type == "audio/ogg"


class TestOutgoingMessageFields:
    def test_required_fields(self):
        msg = make_outgoing("reply", channel="discord", chat_id="789")
        assert msg.channel == "discord"
        assert msg.chat_id == "789"
        assert text_from_parts(msg.parts) == "reply"

    def test_stream_phase_defaults_none(self):
        msg = make_outgoing("x")
        assert msg.stream_phase is None


class TestConcurrentProducers:
    @pytest.mark.asyncio
    async def test_multiple_producers_all_messages_received(self):
        bus = Bus()
        count = 10

        async def produce(i: int):
            await bus.publish_incoming(make_incoming(str(i)))

        await asyncio.gather(*[produce(i) for i in range(count)])

        texts = set()
        for _ in range(count):
            m = await bus.consume_incoming()
            texts.add(text_from_parts(m.parts))

        assert texts == {str(i) for i in range(count)}


class TestConcurrentConsumers:
    @pytest.mark.asyncio
    async def test_each_message_consumed_exactly_once(self):
        bus = Bus()
        n = 5
        for i in range(n):
            await bus.publish_incoming(make_incoming(str(i)))

        results = await asyncio.gather(*[bus.consume_incoming() for _ in range(n)])
        texts = {text_from_parts(m.parts) for m in results}
        assert len(texts) == n


class TestQueueEmptyBehavior:
    @pytest.mark.asyncio
    async def test_consume_blocks_on_empty_incoming(self):
        bus = Bus()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bus.consume_incoming(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_consume_blocks_on_empty_outgoing(self):
        bus = Bus()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bus.consume_outgoing(), timeout=0.05)
