"""Tests for Bus: two-queue message bus for channel ↔ runtime communication."""
import asyncio
import pytest
from operator_use.bus.service import Bus, EventBus
from operator_use.bus.types import IncomingMessage, OutgoingMessage, TextPart


def make_incoming(text: str = "hello", channel: str = "stdio", chat_id: str = "1") -> IncomingMessage:
    return IncomingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


def make_outgoing(text: str = "reply", channel: str = "stdio", chat_id: str = "1") -> OutgoingMessage:
    return OutgoingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


# ── alias ─────────────────────────────────────────────────────────────────────

class TestEventBusAlias:
    def test_eventbus_is_bus(self):
        assert EventBus is Bus


# ── incoming queue ────────────────────────────────────────────────────────────

class TestIncomingQueue:
    @pytest.mark.asyncio
    async def test_publish_and_consume_incoming(self):
        bus = Bus()
        msg = make_incoming("hello")
        await bus.publish_incoming(msg)
        result = await bus.consume_incoming()
        assert result is msg

    @pytest.mark.asyncio
    async def test_incoming_fifo_order(self):
        bus = Bus()
        msgs = [make_incoming(t) for t in ("first", "second", "third")]
        for m in msgs:
            await bus.publish_incoming(m)
        for expected in msgs:
            assert await bus.consume_incoming() is expected

    @pytest.mark.asyncio
    async def test_incoming_blocks_until_message(self):
        bus = Bus()
        msg = make_incoming("late")

        async def producer():
            await asyncio.sleep(0.01)
            await bus.publish_incoming(msg)

        asyncio.create_task(producer())
        result = await asyncio.wait_for(bus.consume_incoming(), timeout=1.0)
        assert result is msg


# ── outgoing queue ────────────────────────────────────────────────────────────

class TestOutgoingQueue:
    @pytest.mark.asyncio
    async def test_publish_and_consume_outgoing(self):
        bus = Bus()
        msg = make_outgoing("reply")
        await bus.publish_outgoing(msg)
        result = await bus.consume_outgoing()
        assert result is msg

    @pytest.mark.asyncio
    async def test_outgoing_fifo_order(self):
        bus = Bus()
        msgs = [make_outgoing(t) for t in ("a", "b")]
        for m in msgs:
            await bus.publish_outgoing(m)
        for expected in msgs:
            assert await bus.consume_outgoing() is expected


# ── queue independence ────────────────────────────────────────────────────────

class TestQueueIndependence:
    @pytest.mark.asyncio
    async def test_incoming_and_outgoing_are_separate(self):
        bus = Bus()
        inc = make_incoming("in")
        out = make_outgoing("out")

        await bus.publish_incoming(inc)
        await bus.publish_outgoing(out)

        assert await bus.consume_incoming() is inc
        assert await bus.consume_outgoing() is out

    @pytest.mark.asyncio
    async def test_separate_bus_instances_do_not_share_state(self):
        bus_a = Bus()
        bus_b = Bus()
        msg = make_incoming("only-a")
        await bus_a.publish_incoming(msg)

        assert bus_b._incoming.empty()
        assert await bus_a.consume_incoming() is msg
