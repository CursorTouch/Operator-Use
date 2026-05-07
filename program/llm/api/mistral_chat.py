from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from mistralai.client.sdk import Mistral
from mistralai.client.models.thinkchunk import ThinkChunk
from mistralai.client.models.textchunk import TextChunk
from mistralai.client.types import UNSET_SENTINEL
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

_STOP_REASON: dict[str, StopReason] = {
    "stop": StopReason.Stop,
    "length": StopReason.Length,
    "model_length": StopReason.Length,
    "tool_calls": StopReason.ToolCalls,
    "error": StopReason.Abort,
}

_MINIMAL_LEVELS = {ThinkingLevel.Low, ThinkingLevel.Minimal}


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


def _messages_to_mistral(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({"role": "system", "content": text})
        elif isinstance(msg, UserMessage):
            result.append({"role": "user", "content": _user_content(msg.contents)})
        elif isinstance(msg, AssistantMessage):
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            content_chunks: list[dict[str, Any]] = []
            has_thinking = any(isinstance(c, ThinkingContent) for c in msg.contents)
            for item in msg.contents:
                if isinstance(item, ThinkingContent):
                    content_chunks.append({
                        "type": "thinking",
                        "thinking": [{"type": "text", "text": item.content}],
                        "signature": item.signature,
                    })
                elif isinstance(item, TextContent):
                    if has_thinking:
                        content_chunks.append({"type": "text", "text": item.content})
                    else:
                        text_parts.append(item.content)
                elif isinstance(item, ToolCallContent):
                    tool_calls.append({
                        "id": item.id,
                        "type": "function",
                        "function": {"name": item.name, "arguments": json.dumps(item.args)},
                    })
            entry: dict[str, Any] = {"role": "assistant"}
            if has_thinking:
                entry["content"] = content_chunks
            else:
                text = "".join(text_parts) or None
                if text is not None:
                    entry["content"] = text
            if tool_calls:
                entry["tool_calls"] = tool_calls
            result.append(entry)
        elif isinstance(msg, ToolMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({"role": "tool", "tool_call_id": msg.id, "content": text})
    return result


class MistralChatAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = Mistral(
            api_key=options.api_key,
            server_url=options.base_url,
            timeout_ms=int(options.timeout.total_seconds() * 1000),
        )

    async def stream(self, messages: list[BaseMessage], model: str = "mistral-medium-latest") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        mistral_messages = _messages_to_mistral(messages)

        reasoning_effort = None
        if self.options.thinking_level is not None:
            reasoning_effort = "none" if self.options.thinking_level in _MINIMAL_LEVELS else "high"

        text_started = False
        text_buf = ""
        text_index = 0
        thinking_started = False
        thinking_buf = ""
        thinking_index = 0
        tool_index = 0
        tool_started: dict[int, bool] = {}
        tool_bufs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, str]] = {}

        yield StartEvent()

        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": mistral_messages,
                "temperature": self.options.temperature,
                "max_tokens": self.options.max_tokens,
            }
            if reasoning_effort is not None:
                kwargs["reasoning_effort"] = reasoning_effort

            if self.options.on_payload:
                modified = self.options.on_payload(kwargs)
                if modified is not None:
                    kwargs = modified

            async with await self._client.chat.stream_async(**kwargs) as stream:
                async for event in stream:
                    if self._cancelled():
                        yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                        return
                    chunk = event.data
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    content = delta.content
                    if content and content != UNSET_SENTINEL:
                        if isinstance(content, str):
                            if not text_started:
                                yield TextStartEvent(data=TextEventData(index=text_index))
                                text_started = True
                            text_buf += content
                            yield TextDeltaEvent(data=TextEventData(index=text_index, text=content))
                        elif isinstance(content, list):
                            for chunk_item in content:
                                if isinstance(chunk_item, ThinkChunk):
                                    thinking_text = "".join(
                                        t.text for t in chunk_item.thinking
                                        if isinstance(t, TextChunk)
                                    )
                                    if thinking_text:
                                        if not thinking_started:
                                            yield ThinkingStartEvent(data=ThinkingEventData(index=thinking_index))
                                            thinking_started = True
                                        thinking_buf += thinking_text
                                        yield ThinkingDeltaEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_text))
                                    if chunk_item.closed:
                                        if thinking_started:
                                            yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
                                            thinking_index += 1
                                            thinking_started = False
                                            thinking_buf = ""
                                elif isinstance(chunk_item, TextChunk):
                                    if not text_started:
                                        yield TextStartEvent(data=TextEventData(index=text_index))
                                        text_started = True
                                    text_buf += chunk_item.text
                                    yield TextDeltaEvent(data=TextEventData(index=text_index, text=chunk_item.text))

                    tool_calls = delta.tool_calls
                    if tool_calls and tool_calls != UNSET_SENTINEL:
                        for tc in tool_calls:
                            idx = tc.index if tc.index is not None else 0
                            fn = tc.function
                            args = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments)
                            tc_id = tc.id or ""
                            if idx not in tool_started:
                                tool_started[idx] = True
                                tool_bufs[idx] = ""
                                tool_meta[idx] = {"id": tc_id, "name": fn.name}
                                yield ToolCallStartEvent(data=ToolCallEventData(
                                    index=tool_index + idx,
                                    id=tc_id,
                                    name=fn.name,
                                ))
                            if args:
                                tool_bufs[idx] += args
                                yield ToolCallDeltaEvent(data=ToolCallEventData(
                                    index=tool_index + idx,
                                    id=tc_id,
                                    args=args,
                                ))

                    finish = choice.finish_reason
                    if finish and finish != UNSET_SENTINEL:
                        if thinking_started:
                            yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
                            thinking_index += 1
                            thinking_started = False
                            thinking_buf = ""

                        if text_started:
                            yield TextEndEvent(data=TextEventData(index=text_index, text=text_buf))
                            text_index += 1
                            text_started = False
                            text_buf = ""

                        for idx in sorted(tool_started):
                            yield ToolCallEndEvent(data=ToolCallEventData(
                                index=tool_index + idx,
                                id=tool_meta[idx]["id"],
                                name=tool_meta[idx]["name"],
                                args=tool_bufs[idx],
                            ))
                        if tool_started:
                            tool_index += len(tool_started)
                            tool_started.clear()
                            tool_bufs.clear()
                            tool_meta.clear()

                        stop_reason = _STOP_REASON.get(str(finish), StopReason.Stop)
                        yield DoneEvent(reason=stop_reason)

        except Exception as e:
            yield ErrorEvent(reason=StopReason.Abort, message=str(e))

    async def invoke(self, messages: list[BaseMessage], model: str = "mistral-medium-latest") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
