"""Tests for the Bus message queue."""

import asyncio
import pytest

from operator_use.bus.service import Bus
from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart


def make_incoming(text="hello", channel="telegram", chat_id="123"):
    return IncomingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


def make_outgoing(text="reply", channel="telegram", chat_id="123"):
    return OutgoingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(content=text)])


@pytest.mark.asyncio
async def test_bus_initial_sizes():
    bus = Bus()
    assert bus.incoming_size == 0
    assert bus.outgoing_size == 0


@pytest.mark.asyncio
async def test_publish_consume_incoming():
    bus = Bus()
    msg = make_incoming("test message")
    await bus.publish_incoming(msg)
    assert bus.incoming_size == 1
    result = await bus.consume_incoming()
    assert result.parts[0].content == "test message"
    assert bus.incoming_size == 0


@pytest.mark.asyncio
async def test_publish_consume_outgoing():
    bus = Bus()
    msg = make_outgoing("test reply")
    await bus.publish_outgoing(msg)
    assert bus.outgoing_size == 1
    result = await bus.consume_outgoing()
    assert result.parts[0].content == "test reply"
    assert bus.outgoing_size == 0


@pytest.mark.asyncio
async def test_incoming_fifo_order():
    bus = Bus()
    messages = [make_incoming(f"msg{i}") for i in range(5)]
    for msg in messages:
        await bus.publish_incoming(msg)
    for i in range(5):
        result = await bus.consume_incoming()
        assert result.parts[0].content == f"msg{i}"


@pytest.mark.asyncio
async def test_outgoing_fifo_order():
    bus = Bus()
    messages = [make_outgoing(f"reply{i}") for i in range(5)]
    for msg in messages:
        await bus.publish_outgoing(msg)
    for i in range(5):
        result = await bus.consume_outgoing()
        assert result.parts[0].content == f"reply{i}"


@pytest.mark.asyncio
async def test_incoming_outgoing_are_isolated():
    bus = Bus()
    await bus.publish_incoming(make_incoming("incoming"))
    assert bus.incoming_size == 1
    assert bus.outgoing_size == 0

    await bus.publish_outgoing(make_outgoing("outgoing"))
    assert bus.incoming_size == 1
    assert bus.outgoing_size == 1


@pytest.mark.asyncio
async def test_multiple_messages_size_tracking():
    bus = Bus()
    for i in range(10):
        await bus.publish_incoming(make_incoming(f"msg{i}"))
    assert bus.incoming_size == 10
    for _ in range(10):
        await bus.consume_incoming()
    assert bus.incoming_size == 0
