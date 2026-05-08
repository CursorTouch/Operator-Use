from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from ollama import AsyncClient
from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMEvent, Options, StopReason, ThinkingLevel,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent, TextEventData,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent, ThinkingEventData,
    ToolCallStartEvent, ToolCallEndEvent, ToolCallEventData,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent,
)

_MINIMAL_LEVELS = {ThinkingLevel.Low, ThinkingLevel.Minimal}

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
}


def _messages_to_ollama(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({"role": "system", "content": text})

        elif isinstance(msg, UserMessage):
            text_parts: list[str] = []
            images: list[str] = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    text_parts.append(item.content)
                elif isinstance(item, ImageContent):
                    images.extend(item.to_base64())
            entry: dict[str, Any] = {"role": "user", "content": "\n".join(text_parts)}
            if images:
                entry["images"] = images
            result.append(entry)

        elif isinstance(msg, AssistantMessage):
            text_parts = []
            thinking_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    text_parts.append(item.content)
                elif isinstance(item, ThinkingContent):
                    thinking_parts.append(item.content)
                elif isinstance(item, ToolCallContent):
                    tool_calls.append({
                        "function": {"name": item.name, "arguments": item.args}
                    })
            entry = {"role": "assistant", "content": "\n".join(text_parts)}
            if thinking_parts:
                entry["thinking"] = "\n".join(thinking_parts)
            if tool_calls:
                entry["tool_calls"] = tool_calls
            result.append(entry)

        elif isinstance(msg, ToolMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({"role": "tool", "content": text})

    return result


class OllamaChatAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = AsyncClient(
            host=options.base_url,
            headers=options.headers or {},
            timeout=options.timeout.total_seconds(),
        )

    def _inference_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {"temperature": self.options.temperature}
        if self.options.max_tokens is not None:
            opts["num_predict"] = self.options.max_tokens
        return opts

    async def stream(self, messages: list[BaseMessage], model: str = "llama3.2") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        ollama_messages = _messages_to_ollama(messages)

        think: bool | None = None
        if self.options.thinking_level is not None:
            think = self.options.thinking_level not in _MINIMAL_LEVELS

        text_started = False
        text_buf = ""
        thinking_started = False
        thinking_buf = ""

        yield StartEvent()

        try:
            payload: dict[str, Any] = {
                "model": model,
                "messages": ollama_messages,
                "stream": True,
                "think": think,
                "options": self._inference_options(),
            }

            if self.options.on_payload:
                modified = self.options.on_payload(payload)
                if modified is not None:
                    payload = modified

            async for chunk in await self._client.chat(**payload):
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                    return
                msg = chunk.message

                if msg.thinking:
                    if not thinking_started:
                        yield ThinkingStartEvent(data=ThinkingEventData(index=0))
                        thinking_started = True
                    thinking_buf += msg.thinking
                    yield ThinkingDeltaEvent(data=ThinkingEventData(index=0, thinking=msg.thinking))

                if msg.content:
                    if not text_started:
                        yield TextStartEvent(data=TextEventData(index=0))
                        text_started = True
                    text_buf += msg.content
                    yield TextDeltaEvent(data=TextEventData(index=0, text=msg.content))

                # tool calls arrive in the final chunk
                if msg.tool_calls:
                    for i, tc in enumerate(msg.tool_calls):
                        fn = tc.function
                        args = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments)
                        yield ToolCallStartEvent(data=ToolCallEventData(index=i, name=fn.name))
                        yield ToolCallEndEvent(data=ToolCallEventData(index=i, name=fn.name, args=args))

                if chunk.done:
                    if thinking_started:
                        yield ThinkingEndEvent(data=ThinkingEventData(index=0, thinking=thinking_buf))
                    if text_started:
                        yield TextEndEvent(data=TextEventData(index=0, text=text_buf))
                    stop_reason = _STOP_REASON.get(chunk.done_reason or "", StopReason.Stop)
                    yield EndEvent(reason=stop_reason)

        except Exception as e:
            yield ErrorEvent(reason=StopReason.Abort, message=str(e))

    async def invoke(self, messages: list[BaseMessage], model: str = "llama3.2") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
