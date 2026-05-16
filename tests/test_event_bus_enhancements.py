"""Tests for EventBus enhancements: once(), emit_async(), channels(), subscriber_count()."""
import asyncio
import pytest
from program.bus.service import EventBus


class TestOnce:
    def test_handler_called_exactly_once(self):
        bus = EventBus()
        received = []
        bus.once("ch", lambda d: received.append(d))
        bus.emit("ch", 1)
        bus.emit("ch", 2)
        assert received == [1]

    def test_once_does_not_affect_persistent_handlers(self):
        bus = EventBus()
        persistent, one_shot = [], []
        bus.on("ch", lambda d: persistent.append(d))
        bus.once("ch", lambda d: one_shot.append(d))
        bus.emit("ch", "a")
        bus.emit("ch", "b")
        assert persistent == ["a", "b"]
        assert one_shot == ["a"]

    def test_once_unsubscribe_before_emit(self):
        bus = EventBus()
        received = []
        unsub = bus.once("ch", lambda d: received.append(d))
        unsub()
        bus.emit("ch", "x")
        assert received == []

    def test_multiple_once_handlers_each_fire_once(self):
        bus = EventBus()
        a, b = [], []
        bus.once("ch", lambda d: a.append(d))
        bus.once("ch", lambda d: b.append(d))
        bus.emit("ch", 1)
        bus.emit("ch", 2)
        assert a == [1]
        assert b == [1]


class TestEmitAsync:
    @pytest.mark.asyncio
    async def test_async_handler_awaited(self):
        bus = EventBus()
        received = []

        async def handler(data):
            await asyncio.sleep(0)
            received.append(data)

        bus.on("ch", handler)
        await bus.emit_async("ch", 42)
        assert received == [42]

    @pytest.mark.asyncio
    async def test_sync_handler_works_in_emit_async(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: received.append(d))
        await bus.emit_async("ch", "hello")
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_handlers_called_sequentially(self):
        bus = EventBus()
        order = []

        async def h1(d):
            order.append("h1-start")
            await asyncio.sleep(0)
            order.append("h1-end")

        async def h2(d):
            order.append("h2-start")
            await asyncio.sleep(0)
            order.append("h2-end")

        bus.on("ch", h1)
        bus.on("ch", h2)
        await bus.emit_async("ch", None)
        assert order == ["h1-start", "h1-end", "h2-start", "h2-end"]

    @pytest.mark.asyncio
    async def test_error_in_async_handler_does_not_crash(self, capsys):
        bus = EventBus()
        received = []

        async def bad(data):
            raise RuntimeError("boom")

        bus.on("ch", bad)
        bus.on("ch", lambda d: received.append("ok"))
        await bus.emit_async("ch", None)
        assert "ok" in received


class TestChannels:
    def test_empty_bus_has_no_channels(self):
        bus = EventBus()
        assert bus.channels() == []

    def test_subscribed_channel_appears(self):
        bus = EventBus()
        bus.on("alpha", lambda d: None)
        assert "alpha" in bus.channels()

    def test_unsubscribed_channel_disappears(self):
        bus = EventBus()
        unsub = bus.on("alpha", lambda d: None)
        unsub()
        assert "alpha" not in bus.channels()

    def test_multiple_channels(self):
        bus = EventBus()
        bus.on("a", lambda d: None)
        bus.on("b", lambda d: None)
        assert set(bus.channels()) == {"a", "b"}

    def test_clear_removes_all_channels(self):
        bus = EventBus()
        bus.on("x", lambda d: None)
        bus.on("y", lambda d: None)
        bus.clear()
        assert bus.channels() == []


class TestSubscriberCount:
    def test_zero_for_unknown_channel(self):
        bus = EventBus()
        assert bus.subscriber_count("nope") == 0

    def test_counts_correctly(self):
        bus = EventBus()
        bus.on("ch", lambda d: None)
        bus.on("ch", lambda d: None)
        assert bus.subscriber_count("ch") == 2

    def test_decreases_after_unsub(self):
        bus = EventBus()
        unsub = bus.on("ch", lambda d: None)
        bus.on("ch", lambda d: None)
        unsub()
        assert bus.subscriber_count("ch") == 1

    def test_zero_after_clear(self):
        bus = EventBus()
        bus.on("ch", lambda d: None)
        bus.clear()
        assert bus.subscriber_count("ch") == 0
