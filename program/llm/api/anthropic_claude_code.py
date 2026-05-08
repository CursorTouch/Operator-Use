from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from anthropic import AsyncAnthropic
from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMEvent, Options, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent, TextEventData,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent, ThinkingEventData,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallEventData,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent,
)

_STOP_REASON: dict[str, StopReason] = {
    "end_turn": StopReason.Stop,
    "max_tokens": StopReason.Length,
    "tool_use": StopReason.ToolCalls,
    "stop_sequence": StopReason.Stop,
}

_DEFAULT_MAX_TOKENS = 8096


def _messages_to_anthropic(
    messages: list[BaseMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    system: str | None = None
    result: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
        elif isinstance(msg, UserMessage):
            parts: list[dict[str, Any]] = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append({"type": "text", "text": item.content})
                elif isinstance(item, ImageContent):
                    for b64 in item.to_base64():
                        parts.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        })
            result.append({"role": "user", "content": parts})
        elif isinstance(msg, AssistantMessage):
            parts = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append({"type": "text", "text": item.content})
                elif isinstance(item, ThinkingContent):
                    parts.append({"type": "thinking", "thinking": item.content, "signature": item.signature})
                elif isinstance(item, ToolCallContent):
                    parts.append({"type": "tool_use", "id": item.id, "name": item.name, "input": item.args})
            result.append({"role": "assistant", "content": parts})
        elif isinstance(msg, ToolMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.id, "content": text}],
            })

    return system, result


class AnthropicClaudeCodeAPI(BaseAPI):
    """Anthropic Messages API using OAuth Bearer token auth (Claude Pro/Max)."""

    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = AsyncAnthropic(
            auth_token=options.api_key,
            base_url=options.base_url,
            default_headers=options.headers,
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(
        self,
        model: str,
        system: str | None,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": self.options.max_tokens or _DEFAULT_MAX_TOKENS,
            "temperature": self.options.temperature,
        }
        if system:
            params["system"] = system
        if self.options.thinking_budget is not None:
            params["thinking"] = {"type": "enabled", "budget_tokens": self.options.thinking_budget}
        return params

    async def stream(self, messages: list[BaseMessage], model: str = "claude-sonnet-4-6") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        system, anthropic_messages = _messages_to_anthropic(messages)
        params = self._build_params(model, system, anthropic_messages)

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        block_types: dict[int, str] = {}
        tool_ids: dict[int, str] = {}
        tool_names: dict[int, str] = {}
        text_bufs: dict[int, str] = {}
        thinking_bufs: dict[int, str] = {}
        tool_bufs: dict[int, str] = {}

        yield StartEvent()

        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                    return
                etype = event.type

                if etype == "content_block_start":
                    idx = event.index
                    block = event.content_block
                    block_types[idx] = block.type
                    if block.type == "text":
                        text_bufs[idx] = ""
                        yield TextStartEvent(data=TextEventData())
                    elif block.type == "thinking":
                        thinking_bufs[idx] = ""
                        yield ThinkingStartEvent(data=ThinkingEventData())
                    elif block.type == "tool_use":
                        tool_ids[idx] = block.id
                        tool_names[idx] = block.name
                        tool_bufs[idx] = ""
                        yield ToolCallStartEvent(data=ToolCallEventData(
                            tool_call=ToolCallContent(id=block.id, name=block.name)
                        ))

                elif etype == "content_block_delta":
                    idx = event.index
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_bufs[idx] = text_bufs.get(idx, "") + delta.text
                        yield TextDeltaEvent(data=TextEventData(text=TextContent(content=delta.text)))
                    elif delta.type == "thinking_delta":
                        thinking_bufs[idx] = thinking_bufs.get(idx, "") + delta.thinking
                        yield ThinkingDeltaEvent(data=ThinkingEventData(thinking=ThinkingContent(content=delta.thinking)))
                    elif delta.type == "input_json_delta":
                        tool_bufs[idx] = tool_bufs.get(idx, "") + delta.partial_json
                        yield ToolCallDeltaEvent(data=ToolCallEventData(
                            tool_call=ToolCallContent(id=tool_ids.get(idx, ""))
                        ))

                elif etype == "content_block_stop":
                    idx = event.index
                    btype = block_types.get(idx, "")
                    if btype == "text":
                        yield TextEndEvent(data=TextEventData(text=TextContent(content=text_bufs.get(idx, ""))))
                    elif btype == "thinking":
                        yield ThinkingEndEvent(data=ThinkingEventData(thinking=ThinkingContent(content=thinking_bufs.get(idx, ""))))
                    elif btype == "tool_use":
                        yield ToolCallEndEvent(data=ToolCallEventData(
                            tool_call=ToolCallContent(
                                id=tool_ids.get(idx, ""),
                                name=tool_names.get(idx, ""),
                                args=json.loads(tool_bufs.get(idx, "{}"))
                            )
                        ))

                elif etype == "message_delta":
                    stop_reason = _STOP_REASON.get(event.delta.stop_reason or "", StopReason.Stop)
                    yield EndEvent(reason=stop_reason)

                elif etype == "error":
                    yield ErrorEvent(reason=StopReason.Abort, message=str(event))

    async def invoke(self, messages: list[BaseMessage], model: str = "claude-sonnet-4-6") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
