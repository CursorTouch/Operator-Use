from __future__ import annotations
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from operator_use.inference.api.text.base import BaseLLMAPI as BaseAPI
from operator_use.inference.model.types import Model
from operator_use.inference.provider.oauth.github_copilot import get_copilot_base_url
from operator_use.inference.types import (
    LLMContext, LLMEvent, LLMOptions, StopReason, ThinkingLevel,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
    normalize_structured_response_format,
)
from operator_use.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ToolCallContent, ToolResultContent,
)
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.tool.types import Tool

_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
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
        match item:
            case TextContent():
                parts.append({"type": "text", "text": item.content})
            case ImageContent():
                for b64, mime in item.to_base64():
                    url = b64 if b64.startswith("http") else f"data:{mime or 'image/png'};base64,{b64}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def _assistant_content(content_items: list) -> tuple[str | None, list[dict[str, Any]]]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in content_items:
        match item:
            case TextContent():
                text_parts.append(item.content)
            case ToolCallContent():
                tool_calls.append({
                    "id": item.id,
                    "type": "function",
                    "function": {"name": item.name, "arguments": json.dumps(item.args)},
                })
    return "".join(text_parts) or None, tool_calls


def _messages_to_chat(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        match msg:
            case SystemMessage():
                text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
                result.append({"role": "system", "content": text})
            case UserMessage():
                result.append({"role": "user", "content": _user_content(msg.contents)})
            case AssistantMessage():
                text, tool_calls = _assistant_content(msg.contents)
                entry: dict[str, Any] = {"role": "assistant"}
                if text is not None:
                    entry["content"] = text
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                result.append(entry)
            case ToolMessage():
                for content in msg.contents:
                    if isinstance(content, ToolResultContent):
                        result.append({
                            "role": "tool",
                            "tool_call_id": content.id,
                            "content": content.content,
                        })
    return result


def _response_format(response_format: Any | None) -> dict[str, Any] | None:
    structured = normalize_structured_response_format(response_format)
    if structured is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": structured.name,
            "schema": structured.schema,
            "strict": structured.strict,
        },
    }


class GitHubCopilotChatAPI(BaseAPI):
    def __init__(self, options: LLMOptions) -> None:
        super().__init__(options)
        base_url = options.base_url or get_copilot_base_url(options.api_key)
        self._client = AsyncOpenAI(
            api_key=options.api_key or "github-copilot",
            base_url=base_url,
            default_headers={**_COPILOT_HEADERS, **(options.headers or {})},
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(self, model: Model, messages: list[dict[str, Any]], tools: Optional[list[Tool]] = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_completion_tokens"] = self.options.max_tokens

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

    async def stream(self, context: LLMContext, model: Model) -> AsyncGenerator[LLMEvent, None]:  # type: ignore[override]
        chat_messages = _messages_to_chat(context.messages)
        if context.system_prompt:
            chat_messages = [{"role": "system", "content": context.system_prompt}] + chat_messages
        params = self._build_params(model, chat_messages, tools=context.tools or None)
        response_format = _response_format(context.response_format)
        if response_format is not None:
            params["response_format"] = response_format

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        text_started = False
        text_buf = ""
        tool_started: dict[int, bool] = {}
        tool_bufs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, str]] = {}
        _input_tokens = 0
        _output_tokens = 0

        yield StartEvent()

        async for chunk in await self._client.chat.completions.create(**params, stream=True, stream_options={"include_usage": True}):
            if self._cancelled():
                yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                return
            usage_data = getattr(chunk, 'usage', None)
            if usage_data:
                _input_tokens = getattr(usage_data, 'prompt_tokens', 0) or 0
                _output_tokens = getattr(usage_data, 'completion_tokens', 0) or 0
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
                        tool_meta[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name or "" if tc.function else "",
                        }
                        yield ToolCallStartEvent(tool_call=ToolCallContent(
                                id=tool_meta[idx]["id"],
                                name=tool_meta[idx]["name"],
                            ))
                    if tc.function and tc.function.arguments:
                        tool_bufs[idx] += tc.function.arguments
                        yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_meta[idx]["id"]))

            if choice.finish_reason:
                if text_started:
                    yield TextEndEvent(text=TextContent(content=text_buf))
                    text_started = False
                    text_buf = ""

                for idx in sorted(tool_started):
                    args_str = tool_bufs[idx].strip()
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {}

                    yield ToolCallEndEvent(tool_call=ToolCallContent(
                            id=tool_meta[idx]["id"],
                            name=tool_meta[idx]["name"],
                            args=args,
                        ))
                tool_started.clear()
                tool_bufs.clear()
                tool_meta.clear()

                stop_reason = _STOP_REASON.get(choice.finish_reason, StopReason.Stop)
                yield EndEvent(reason=stop_reason, input_tokens=_input_tokens, output_tokens=_output_tokens)

    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(context, model=model):
            events.append(event)
        return events
