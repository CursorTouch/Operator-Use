"""Tests for EventBus: subscribe, publish, unsubscribe, error isolation."""
import asyncio
import pytest
from program.bus.service import EventBus


# ── subscribe and emit ────────────────────────────────────────────────────────

class TestEventBusBasic:
    def test_handler_called_on_emit(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda data: received.append(data))
        bus.emit("ch", 42)
        assert received == [42]

    def test_multiple_handlers_on_same_channel(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: received.append("a"))
        bus.on("ch", lambda d: received.append("b"))
        bus.emit("ch", None)
        assert set(received) == {"a", "b"}

    def test_emit_to_empty_channel_is_noop(self):
        bus = EventBus()
        bus.emit("nobody", "data")  # no error

    def test_emit_no_data_defaults_to_none(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: received.append(d))
        bus.emit("ch")
        assert received == [None]

    def test_data_passed_correctly(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: received.append(d))
        bus.emit("ch", {"key": "value"})
        assert received == [{"key": "value"}]

    def test_multiple_channels_isolated(self):
        bus = EventBus()
        a, b = [], []
        bus.on("a", lambda d: a.append(d))
        bus.on("b", lambda d: b.append(d))
        bus.emit("a", 1)
        assert a == [1]
        assert b == []


# ── unsubscribe ───────────────────────────────────────────────────────────────

class TestUnsubscribe:
    def test_unsubscribe_stops_handler(self):
        bus = EventBus()
        received = []
        unsub = bus.on("ch", lambda d: received.append(d))
        bus.emit("ch", 1)
        unsub()
        bus.emit("ch", 2)
        assert received == [1]

    def test_double_unsubscribe_is_safe(self):
        bus = EventBus()
        unsub = bus.on("ch", lambda d: None)
        unsub()
        unsub()  # should not raise

    def test_other_handlers_unaffected_after_unsub(self):
        bus = EventBus()
        a, b = [], []
        unsub_a = bus.on("ch", lambda d: a.append(d))
        bus.on("ch", lambda d: b.append(d))
        unsub_a()
        bus.emit("ch", 99)
        assert a == []
        assert b == [99]


# ── error isolation ───────────────────────────────────────────────────────────

class TestErrorIsolation:
    def test_handler_exception_does_not_crash_emit(self, capsys):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: (_ for _ in ()).throw(ValueError("bad")))
        bus.on("ch", lambda d: received.append("ok"))
        bus.emit("ch", None)
        assert "ok" in received

    def test_error_printed_to_stdout(self, capsys):
        bus = EventBus()
        def bad(d): raise RuntimeError("test error")
        bus.on("ch", bad)
        bus.emit("ch", None)
        out = capsys.readouterr().out
        assert "EventBus" in out or "test error" in out


# ── clear ─────────────────────────────────────────────────────────────────────

class TestClear:
    def test_clear_removes_all_subscriptions(self):
        bus = EventBus()
        received = []
        bus.on("ch", lambda d: received.append(d))
        bus.clear()
        bus.emit("ch", 1)
        assert received == []

    def test_clear_affects_all_channels(self):
        bus = EventBus()
        a, b = [], []
        bus.on("a", lambda d: a.append(d))
        bus.on("b", lambda d: b.append(d))
        bus.clear()
        bus.emit("a", 1)
        bus.emit("b", 2)
        assert a == [] and b == []


# ── async handler ─────────────────────────────────────────────────────────────

class TestAsyncHandler:
    @pytest.mark.asyncio
    async def test_async_handler_scheduled(self):
        bus = EventBus()
        received = []

        async def handler(data):
            received.append(data)

        bus.on("ch", handler)
        bus.emit("ch", "ping")
        await asyncio.sleep(0)  # allow event loop to run the coroutine
        assert "ping" in received
