"""Tests for Gateway channel management and dispatch."""

import asyncio
import pytest
from unittest.mock import MagicMock

from operator_use.bus.service import Bus
from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart
from operator_use.gateway.service import Gateway
from operator_use.gateway.channels.base import BaseChannel


# --- Concrete channel stub ---


class StubChannel(BaseChannel):
    def __init__(self, channel_name: str, account_id: str = "", bus=None):
        config = MagicMock()
        config.account_id = account_id
        super().__init__(config=config, bus=bus)
        self._name = channel_name
        self.sent_messages: list = []

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.running = False

    async def _listen(self) -> None:
        pass

    async def send(self, message: OutgoingMessage) -> int | None:
        self.sent_messages.append(message)
        return 42


# --- add_channel / get_channel / list_channels ---


def test_add_and_get_channel():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    gw.add_channel(ch)
    assert gw.get_channel("telegram") is ch


def test_add_channel_with_account_id():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram", account_id="bot1")
    gw.add_channel(ch)
    assert gw.get_channel("telegram:bot1") is ch


def test_get_channel_missing_returns_none():
    bus = Bus()
    gw = Gateway(bus)
    assert gw.get_channel("slack") is None


def test_list_channels():
    bus = Bus()
    gw = Gateway(bus)
    ch1 = StubChannel("telegram")
    ch2 = StubChannel("discord")
    gw.add_channel(ch1)
    gw.add_channel(ch2)
    channels = gw.list_channels()
    assert ch1 in channels
    assert ch2 in channels


def test_add_channel_sets_bus():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram", bus=None)
    assert ch.bus is None
    gw.add_channel(ch)
    assert ch.bus is bus


# --- enable / disable channel ---


@pytest.mark.asyncio
async def test_enable_channel_not_found():
    bus = Bus()
    gw = Gateway(bus)
    result = await gw.enable_channel("ghost")
    assert result is False


@pytest.mark.asyncio
async def test_enable_channel_already_running():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    ch.running = True
    gw.add_channel(ch)
    result = await gw.enable_channel("telegram")
    assert result is False


@pytest.mark.asyncio
async def test_enable_channel_starts_it():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    ch.running = False
    gw.add_channel(ch)
    result = await gw.enable_channel("telegram")
    assert result is True
    assert ch.running is True


@pytest.mark.asyncio
async def test_disable_channel_not_found():
    bus = Bus()
    gw = Gateway(bus)
    result = await gw.disable_channel("ghost")
    assert result is False


@pytest.mark.asyncio
async def test_disable_channel_already_stopped():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    ch.running = False
    gw.add_channel(ch)
    result = await gw.disable_channel("telegram")
    assert result is False


@pytest.mark.asyncio
async def test_disable_channel_stops_it():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    ch.running = True
    gw.add_channel(ch)
    result = await gw.disable_channel("telegram")
    assert result is True
    assert ch.running is False


# --- BaseChannel.receive ---


@pytest.mark.asyncio
async def test_base_channel_receive_publishes_to_bus():
    bus = Bus()
    ch = StubChannel("telegram", bus=bus)
    msg = IncomingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="hi")])
    await ch.receive(msg)
    assert bus.incoming_size == 1
    received = await bus.consume_incoming()
    assert received.parts[0].content == "hi"


@pytest.mark.asyncio
async def test_base_channel_receive_no_bus_no_error():
    ch = StubChannel("telegram", bus=None)
    msg = IncomingMessage(channel="telegram", chat_id="1")
    await ch.receive(msg)  # Should not raise


# --- dispatch loop ---


@pytest.mark.asyncio
async def test_dispatch_routes_to_correct_channel():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    gw.add_channel(ch)

    gw._running = True
    dispatch_task = asyncio.create_task(gw._dispatch_loop())

    msg = OutgoingMessage(channel="telegram", chat_id="1", parts=[TextPart(content="hello")])
    await bus.publish_outgoing(msg)
    await asyncio.sleep(0.1)

    gw._running = False
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    assert len(ch.sent_messages) == 1
    assert ch.sent_messages[0].parts[0].content == "hello"


@pytest.mark.asyncio
async def test_dispatch_with_account_id_routes_correctly():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram", account_id="bot1")
    gw.add_channel(ch)

    gw._running = True
    dispatch_task = asyncio.create_task(gw._dispatch_loop())

    msg = OutgoingMessage(
        channel="telegram", chat_id="1", account_id="bot1", parts=[TextPart(content="routed")]
    )
    await bus.publish_outgoing(msg)
    await asyncio.sleep(0.1)

    gw._running = False
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    assert len(ch.sent_messages) == 1


@pytest.mark.asyncio
async def test_dispatch_resolves_sent_id_future():
    bus = Bus()
    gw = Gateway(bus)
    ch = StubChannel("telegram")
    gw.add_channel(ch)

    gw._running = True
    dispatch_task = asyncio.create_task(gw._dispatch_loop())

    loop = asyncio.get_event_loop()
    future = loop.create_future()
    msg = OutgoingMessage(
        channel="telegram", chat_id="1", parts=[TextPart(content="hi")], sent_id_future=future
    )
    await bus.publish_outgoing(msg)
    await asyncio.sleep(0.1)

    gw._running = False
    dispatch_task.cancel()
    try:
        await dispatch_task
    except asyncio.CancelledError:
        pass

    assert future.done()
    assert future.result() == 42
