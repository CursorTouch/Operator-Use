from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from program.inference.api.llm.openai_codex_responses import _process_events
from program.inference.types import EndEvent, StopReason, ToolCallEndEvent


async def _iter_events(items: list[dict]) -> AsyncIterator[dict]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_process_events_treats_function_call_turn_as_tool_calls_when_stop_reason_is_stop():
    events = [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call_123",
                "name": "web_fetch",
                "arguments": "{\"url\":\"https://news.ycombinator.com\"}",
            },
        },
        {
            "type": "response.completed",
            "response": {
                "stop_reason": "stop",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    ]

    seen = [event async for event in _process_events(_iter_events(events))]

    assert any(isinstance(event, ToolCallEndEvent) for event in seen)
    end = next(event for event in seen if isinstance(event, EndEvent))
    assert end.reason == StopReason.ToolCalls


@pytest.mark.asyncio
async def test_process_events_treats_function_call_turn_as_tool_calls_when_stop_reason_missing():
    events = [
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "fc_123",
                "call_id": "call_123",
                "name": "terminal",
            },
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_123",
            "arguments": "{\"cmd\":\"uname -a\"}",
        },
        {
            "type": "response.completed",
            "response": {
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        },
    ]

    seen = [event async for event in _process_events(_iter_events(events))]

    end = next(event for event in seen if isinstance(event, EndEvent))
    assert end.reason == StopReason.ToolCalls
