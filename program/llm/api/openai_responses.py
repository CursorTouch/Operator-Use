from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMEvent, Options, StopReason, ThinkingLevel,
    StartEvent, DoneEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent, TextEventData,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent, ThinkingEventData,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallEventData,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent,
)

_THINKING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Low: "low",
    ThinkingLevel.Minimal: "minimal",
    ThinkingLevel.Medium: "medium",
    ThinkingLevel.High: "high",
    ThinkingLevel.XHigh: "xhigh",
    ThinkingLevel.Max: "max",
}

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "max_output_tokens": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "content_filter": StopReason.ContentFilter,
}


def _content_to_openai(content_items: list) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for item in content_items:
        if isinstance(item, TextContent):
            parts.append({"type": "input_text", "text": item.content})
        elif isinstance(item, ImageContent):
            for b64 in item.to_base64():
                url = b64 if b64.startswith("http") else f"data:image/png;base64,{b64}"
                parts.append({"type": "input_image", "image_url": url})
        elif isinstance(item, ThinkingContent):
            parts.append({
                "type": "thinking",
                "thinking": item.content,
                "signature": item.signature,
            })
        elif isinstance(item, ToolCallContent):
            parts.append({
                "type": "function_call",
                "call_id": item.id,
                "name": item.name,
                "arguments": json.dumps(item.args),
            })
    return parts


def _messages_to_input(
    messages: list[BaseMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: str | None = None
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            text_parts = [c.content for c in msg.contents if isinstance(c, TextContent)]
            instructions = "\n".join(text_parts)
        elif isinstance(msg, ToolMessage):
            for item in msg.contents:
                if isinstance(item, TextContent):
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": msg.id,
                        "output": item.content,
                    })
        else:
            role = "user" if isinstance(msg, UserMessage) else "assistant"
            parts = _content_to_openai(msg.contents)
            if parts:
                input_items.append({"role": role, "content": parts})

    return instructions, input_items


class OpenAIResponsesAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = AsyncOpenAI(
            api_key=options.api_key,
            base_url=options.base_url,
            default_headers=options.headers,
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(self, model: str, instructions: str | None, input_items: list) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "temperature": self.options.temperature,
        }
        if instructions:
            params["instructions"] = instructions
        if self.options.max_tokens is not None:
            params["max_output_tokens"] = self.options.max_tokens
        if self.options.thinking_level is not None:
            params["reasoning"] = {"effort": _THINKING_EFFORT[self.options.thinking_level]}
        return params

    async def stream(self, messages: list[BaseMessage], model: str = "gpt-4o") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        instructions, input_items = _messages_to_input(messages)
        params = self._build_params(model, instructions, input_items)

        text_index = 0
        thinking_index = 0
        tool_index = 0
        tool_ids: dict[str, str] = {}
        tool_names: dict[str, str] = {}

        yield StartEvent()

        async with self._client.responses.stream(**params) as stream:
            async for event in stream:
                etype = event.type

                if etype == "response.output_item.added":
                    item = event.item
                    if item.type == "message":
                        yield TextStartEvent(data=TextEventData(index=text_index))
                    elif item.type == "reasoning":
                        yield ThinkingStartEvent(data=ThinkingEventData(index=thinking_index))
                    elif item.type == "function_call":
                        tool_ids[item.call_id] = item.call_id
                        tool_names[item.call_id] = item.name
                        yield ToolCallStartEvent(data=ToolCallEventData(
                            index=tool_index,
                            id=item.call_id,
                            name=item.name,
                        ))

                elif etype == "response.output_text.delta":
                    yield TextDeltaEvent(data=TextEventData(index=text_index, text=event.delta))

                elif etype == "response.output_text.done":
                    yield TextEndEvent(data=TextEventData(index=text_index, text=event.text))
                    text_index += 1

                elif etype == "response.reasoning_summary_text.delta":
                    yield ThinkingDeltaEvent(data=ThinkingEventData(index=thinking_index, thinking=event.delta))

                elif etype == "response.reasoning_summary_text.done":
                    yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=event.text))
                    thinking_index += 1

                elif etype == "response.function_call_arguments.delta":
                    call_id = event.item_id
                    yield ToolCallDeltaEvent(data=ToolCallEventData(
                        index=tool_index,
                        id=call_id,
                        args=event.delta,
                    ))

                elif etype == "response.function_call_arguments.done":
                    call_id = event.item_id
                    yield ToolCallEndEvent(data=ToolCallEventData(
                        index=tool_index,
                        id=call_id,
                        name=tool_names.get(call_id, ""),
                        args=event.arguments,
                    ))
                    tool_index += 1

                elif etype == "response.done":
                    resp = event.response
                    raw_reason = getattr(resp, "incomplete_details", None)
                    stop_reason = _STOP_REASON.get(
                        getattr(resp, "stop_reason", None) or "",
                        StopReason.Stop,
                    )
                    yield DoneEvent(reason=stop_reason)

                elif etype == "error":
                    yield ErrorEvent(reason=StopReason.Abort, message=str(event))

    async def invoke(self, messages: list[BaseMessage], model: str = "gpt-4o") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
