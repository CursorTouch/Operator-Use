"""Tests for FollowupQueue and SteeringQueue: modes, enqueue/dequeue, is_empty."""
import pytest
from operator_use.engine.types import FollowupQueue, SteeringQueue, FollowupMode, SteeringMode
from operator_use.message.types import UserMessage


def u(text: str = "msg") -> UserMessage:
    return UserMessage.text(text)


# ── FollowupQueue ─────────────────────────────────────────────────────────────

class TestFollowupQueue:
    @pytest.mark.asyncio
    async def test_empty_on_creation(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_not_empty_after_enqueue(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        await q.enqueue(u())
        assert not q.is_empty()

    @pytest.mark.asyncio
    async def test_one_at_a_time_dequeues_single(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        await q.enqueue(u("a"))
        await q.enqueue(u("b"))
        msgs = await q.dequeue()
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_one_at_a_time_leaves_remainder(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        await q.enqueue(u("a"))
        await q.enqueue(u("b"))
        await q.dequeue()
        assert not q.is_empty()

    @pytest.mark.asyncio
    async def test_all_mode_dequeues_all(self):
        q = FollowupQueue(mode=FollowupMode.All)
        await q.enqueue(u("a"))
        await q.enqueue(u("b"))
        await q.enqueue(u("c"))
        msgs = await q.dequeue()
        assert len(msgs) == 3
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_empty_list(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        msgs = await q.dequeue()
        assert msgs == []

    @pytest.mark.asyncio
    async def test_clear_empties_queue(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        await q.enqueue(u("a"))
        await q.enqueue(u("b"))
        q.clear()
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_order_preserved_one_at_a_time(self):
        q = FollowupQueue(mode=FollowupMode.OneAtATime)
        await q.enqueue(u("first"))
        await q.enqueue(u("second"))
        first = await q.dequeue()
        second = await q.dequeue()
        from operator_use.message.types import TextContent
        assert first[0].contents[0].content == "first"
        assert second[0].contents[0].content == "second"

    @pytest.mark.asyncio
    async def test_all_mode_order_preserved(self):
        q = FollowupQueue(mode=FollowupMode.All)
        for text in ["x", "y", "z"]:
            await q.enqueue(u(text))
        msgs = await q.dequeue()
        from operator_use.message.types import TextContent
        contents = [m.contents[0].content for m in msgs]
        assert contents == ["x", "y", "z"]


# ── SteeringQueue ─────────────────────────────────────────────────────────────

class TestSteeringQueue:
    @pytest.mark.asyncio
    async def test_empty_on_creation(self):
        q = SteeringQueue(mode=SteeringMode.OneAtATime)
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue_one_at_a_time(self):
        q = SteeringQueue(mode=SteeringMode.OneAtATime)
        await q.enqueue(u("steer1"))
        await q.enqueue(u("steer2"))
        msgs = await q.dequeue()
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_all_mode_dequeues_all(self):
        q = SteeringQueue(mode=SteeringMode.All)
        await q.enqueue(u("a"))
        await q.enqueue(u("b"))
        msgs = await q.dequeue()
        assert len(msgs) == 2
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_clear(self):
        q = SteeringQueue(mode=SteeringMode.OneAtATime)
        await q.enqueue(u())
        q.clear()
        assert q.is_empty()
