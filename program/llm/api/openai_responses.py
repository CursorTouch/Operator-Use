from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMContext, LLMEvent, Options, StopReason, ThinkingLevel,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent, ToolResultContent,
)
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from program.tool.types import Tool

_THINKING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Minimal: "low",
    ThinkingLevel.Low: "low",
    ThinkingLevel.Medium: "medium",
    ThinkingLevel.High: "high",
    ThinkingLevel.XHigh: "high",
    ThinkingLevel.Max: "high",
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
            for b64, mime in item.to_base64():
                url = b64 if b64.startswith("http") else f"data:{mime or 'image/png'};base64,{b64}"
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
            for content in msg.contents:
                if isinstance(content, ToolResultContent):
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": content.id,
                        "output": content.content,
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

    def _build_params(self, model: str, instructions: str | None, input_items: list, tools: Optional[list[Tool]] = None) -> dict[str, Any]:
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
        
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.schema.model_json_schema(),
                }
                for tool in tools
            ]

        return params

    async def stream(self, context: LLMContext, model: str = "gpt-4o") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        instructions, input_items = _messages_to_input(context.messages)
        params = self._build_params(model, instructions, input_items, tools=context.tools or None)

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        tool_names: dict[str, str] = {}

        yield StartEvent()

        async with self._client.responses.stream(**params) as stream:
            async for event in stream:
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                etype = event.type

                if etype == "response.output_item.added":
                    item = event.item
                    if item.type == "message":
                        yield TextStartEvent(text=TextContent(content=""))
                    elif item.type == "reasoning":
                        yield ThinkingStartEvent(thinking=None)
                    elif item.type == "function_call":
                        tool_names[item.call_id] = item.name
                        yield ToolCallStartEvent(tool_call=ToolCallContent(id=item.call_id, name=item.name))

                elif etype == "response.output_text.delta":
                    yield TextDeltaEvent(text=TextContent(content=event.delta))

                elif etype == "response.output_text.done":
                    yield TextEndEvent(text=TextContent(content=event.text))

                elif etype == "response.reasoning_summary_text.delta":
                    yield ThinkingDeltaEvent(thinking=ThinkingContent(content=event.delta))

                elif etype == "response.reasoning_summary_text.done":
                    yield ThinkingEndEvent(thinking=ThinkingContent(content=event.text))

                elif etype == "response.function_call_arguments.delta":
                    call_id = event.item_id
                    yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=call_id))

                elif etype == "response.function_call_arguments.done":
                    call_id = event.item_id
                    args_str = event.arguments.strip()
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {}

                    yield ToolCallEndEvent(tool_call=ToolCallContent(
                        id=call_id,
                        name=tool_names.get(call_id, ""),
                        args=args
                    ))

                elif etype == "response.done":
                    resp = event.response
                    raw_reason = getattr(resp, "incomplete_details", None)
                    stop_reason = _STOP_REASON.get(
                        getattr(resp, "stop_reason", None) or "",
                        StopReason.Stop,
                    )
                    yield EndEvent(reason=stop_reason)

                elif etype == "error":
                    yield ErrorEvent(reason=StopReason.Abort, error=str(event))

    async def invoke(self, context: LLMContext, model: str = "gpt-4o") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(context, model=model):
            events.append(event)
        return events
