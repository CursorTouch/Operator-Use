from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from anthropic import AsyncAnthropic
from program.inference.api.llm.base import BaseLLMAPI as BaseAPI
from program.inference.model.types import Model
from program.inference.types import (
    LLMContext, LLMEvent, Options, StopReason,
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
        match msg:
            case SystemMessage():
                system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            case UserMessage():
                parts: list[dict[str, Any]] = []
                has_text = False
                has_image = False
                for item in msg.contents:
                    match item:
                        case TextContent():
                            has_text = True
                            parts.append({"type": "text", "text": item.content})
                        case ImageContent():
                            has_image = True
                            for b64, mime in item.to_base64():
                                parts.append({
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": mime or "image/png", "data": b64},
                                })
                if has_image and not has_text:
                    parts.append({"type": "text", "text": "(see attached image)"})
                result.append({"role": "user", "content": parts})
            case AssistantMessage():
                parts = []
                for item in msg.contents:
                    match item:
                        case TextContent():
                            parts.append({"type": "text", "text": item.content})
                        case ThinkingContent():
                            parts.append({"type": "thinking", "thinking": item.content, "signature": item.signature})
                        case ToolCallContent():
                            parts.append({"type": "tool_use", "id": item.id, "name": item.name, "input": item.args})
                result.append({"role": "assistant", "content": parts})
            case ToolMessage():
                tool_results = []
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": content.id,
                            "content": content.content,
                            "is_error": content.is_error,
                        })
                if tool_results:
                    result.append({
                        "role": "user",
                        "content": tool_results,
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
        model: Model,
        system: str | None,
        messages: list[dict[str, Any]],
        tools: Optional[list[Tool]] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "max_tokens": self.options.max_tokens or _DEFAULT_MAX_TOKENS,
            "temperature": self.options.temperature,
        }
        if system:
            params["system"] = system
        if self.options.thinking_budget is not None:
            params["thinking"] = {"type": "enabled", "budget_tokens": self.options.thinking_budget}
        
        if tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.schema.model_json_schema(),
                }
                for tool in tools
            ]
        return params

    async def stream(self, context: LLMContext, model: Model) -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        system, anthropic_messages = _messages_to_anthropic(context.messages)
        params = self._build_params(model, system, anthropic_messages, tools=context.tools or None)

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
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                etype = event.type

                if etype == "content_block_start":
                    idx = event.index
                    block = event.content_block
                    block_types[idx] = block.type
                    if block.type == "text":
                        text_bufs[idx] = ""
                        yield TextStartEvent(text=TextContent(content=""))
                    elif block.type == "thinking":
                        thinking_bufs[idx] = ""
                        yield ThinkingStartEvent(thinking=None)
                    elif block.type == "tool_use":
                        tool_ids[idx] = block.id
                        tool_names[idx] = block.name
                        tool_bufs[idx] = ""
                        yield ToolCallStartEvent(tool_call=ToolCallContent(id=block.id, name=block.name))

                elif etype == "content_block_delta":
                    idx = event.index
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_bufs[idx] = text_bufs.get(idx, "") + delta.text
                        yield TextDeltaEvent(text=TextContent(content=delta.text))
                    elif delta.type == "thinking_delta":
                        thinking_bufs[idx] = thinking_bufs.get(idx, "") + delta.thinking
                        yield ThinkingDeltaEvent(thinking=ThinkingContent(content=delta.thinking))
                    elif delta.type == "input_json_delta":
                        tool_bufs[idx] = tool_bufs.get(idx, "") + delta.partial_json
                        yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_ids.get(idx, "")))

                elif etype == "content_block_stop":
                    idx = event.index
                    btype = block_types.get(idx, "")
                    if btype == "text":
                        yield TextEndEvent(text=TextContent(content=text_bufs.get(idx, "")))
                    elif btype == "thinking":
                        yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_bufs.get(idx, "")))
                    elif btype == "tool_use":
                        args_str = tool_bufs.get(idx, "").strip()
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}

                        yield ToolCallEndEvent(tool_call=ToolCallContent(
                                id=tool_ids.get(idx, ""),
                                name=tool_names.get(idx, ""),
                                args=args
                            ))

                elif etype == "message_delta":
                    stop_reason = _STOP_REASON.get(event.delta.stop_reason or "", StopReason.Stop)
                    yield EndEvent(reason=stop_reason)

                elif etype == "error":
                    yield ErrorEvent(reason=StopReason.Abort, error=str(event))

    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(context, model=model):
            events.append(event)
        return events
