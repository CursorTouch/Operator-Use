from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMEvent, Options, StopReason, ThinkingLevel,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent, ToolResultContent,
)
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from program.tool.types import Tool

_REASONING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Low: "low",
    ThinkingLevel.Minimal: "low",
    ThinkingLevel.Medium: "medium",
    ThinkingLevel.High: "high",
    ThinkingLevel.XHigh: "xhigh",
    ThinkingLevel.Max: "xhigh",
}

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "content_filter": StopReason.ContentFilter,
}


def _user_content(content_items: list) -> str | list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for item in content_items:
        if isinstance(item, TextContent):
            parts.append({"type": "text", "text": item.content})
        elif isinstance(item, ImageContent):
            for b64 in item.to_base64():
                url = b64 if b64.startswith("http") else f"data:image/png;base64,{b64}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def _assistant_content(content_items: list) -> tuple[str | None, list[dict[str, Any]]]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in content_items:
        if isinstance(item, TextContent):
            text_parts.append(item.content)
        elif isinstance(item, ToolCallContent):
            tool_calls.append({
                "id": item.id,
                "type": "function",
                "function": {"name": item.name, "arguments": json.dumps(item.args)},
            })
    text = "".join(text_parts) or None
    return text, tool_calls


def _messages_to_chat(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = "\n".join(
                c.content for c in msg.contents if isinstance(c, TextContent)
            )
            result.append({"role": "system", "content": text})
        elif isinstance(msg, UserMessage):
            result.append({"role": "user", "content": _user_content(msg.contents)})
        elif isinstance(msg, AssistantMessage):
            text, tool_calls = _assistant_content(msg.contents)
            entry: dict[str, Any] = {"role": "assistant"}
            if text is not None:
                entry["content"] = text
            if tool_calls:
                entry["tool_calls"] = tool_calls
            result.append(entry)
        elif isinstance(msg, ToolMessage):
            for content in msg.contents:
                if isinstance(content, ToolResultContent):
                    result.append({
                        "role": "tool",
                        "tool_call_id": content.id,
                        "content": content.content,
                    })
    return result


class OpenAICompletionsAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = AsyncOpenAI(
            api_key=options.api_key,
            base_url=options.base_url,
            default_headers=options.headers,
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(self, model: str, messages: list[dict[str, Any]], tools: Optional[list[Tool]] = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_completion_tokens"] = self.options.max_tokens
        if self.options.thinking_level is not None:
            params["reasoning_effort"] = _REASONING_EFFORT[self.options.thinking_level]
        
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.schema.model_json_schema(),
                    }
                }
                for tool in tools
            ]
            params["tool_choice"] = "auto"

        return params

    async def stream(self, messages: list[BaseMessage], model: str = "gpt-4o", tools: Optional[list[Tool]] = None) -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        chat_messages = _messages_to_chat(messages)
        params = self._build_params(model, chat_messages, tools=tools)

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        text_started = False
        text_buf = ""
        # tool call state keyed by delta index
        tool_started: dict[int, bool] = {}
        tool_bufs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, str]] = {}

        yield StartEvent()

        async for chunk in await self._client.chat.completions.create(**params, stream=True):
            if self._cancelled():
                yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                return
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            delta = choice.delta

            if delta.content:
                if not text_started:
                    yield TextStartEvent(text=TextContent(content=""))
                    text_started = True
                text_buf += delta.content
                yield TextDeltaEvent(text=TextContent(content=delta.content))

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_started:
                        tool_started[idx] = True
                        tool_bufs[idx] = ""
                        tool_meta[idx] = {"id": tc.id or "", "name": tc.function.name or "" if tc.function else ""}
                        yield ToolCallStartEvent(tool_call=ToolCallContent(
                                id=tool_meta[idx]["id"],
                                name=tool_meta[idx]["name"],
                            )
                        )
                    if tc.function and tc.function.arguments:
                        tool_bufs[idx] += tc.function.arguments
                        yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_meta[idx]["id"])
                        )

            if choice.finish_reason:
                if text_started:
                    yield TextEndEvent(text=TextContent(content=text_buf))
                    text_started = False
                    text_buf = ""

                for idx in sorted(tool_started):
                    yield ToolCallEndEvent(tool_call=ToolCallContent(
                            id=tool_meta[idx]["id"],
                            name=tool_meta[idx]["name"],
                            args=json.loads(tool_bufs[idx]),
                        )
                    )
                tool_started.clear()
                tool_bufs.clear()
                tool_meta.clear()

                stop_reason = _STOP_REASON.get(choice.finish_reason, StopReason.Stop)
                yield EndEvent(reason=stop_reason)

    async def invoke(self, messages: list[BaseMessage], model: str = "gpt-4o", tools: Optional[list[Tool]] = None) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model, tools=tools):
            events.append(event)
        return events
