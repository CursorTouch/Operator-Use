from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from openai import AsyncOpenAI
from program.llm.api.base import BaseAPI
from program.llm.provider.oauth.github_copilot import get_copilot_base_url
from program.llm.types import (
    LLMEvent, Options, StopReason, ThinkingLevel,
    StartEvent, DoneEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent, TextEventData,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallEventData,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ToolCallContent,
)

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
    return "".join(text_parts) or None, tool_calls


def _messages_to_chat(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
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
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            result.append({"role": "tool", "tool_call_id": msg.id, "content": text})
    return result


class GitHubCopilotChatAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        base_url = options.base_url or get_copilot_base_url(options.api_key)
        self._client = AsyncOpenAI(
            api_key=options.api_key or "github-copilot",
            base_url=base_url,
            default_headers={**_COPILOT_HEADERS, **(options.headers or {})},
            max_retries=options.max_retries,
            timeout=options.timeout.total_seconds(),
        )

    def _build_params(self, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_completion_tokens"] = self.options.max_tokens
        return params

    async def stream(self, messages: list[BaseMessage], model: str = "gpt-4o") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        chat_messages = _messages_to_chat(messages)
        params = self._build_params(model, chat_messages)

        if self.options.on_payload:
            modified = self.options.on_payload(params)
            if modified is not None:
                params = modified

        text_started = False
        text_buf = ""
        text_index = 0
        tool_index = 0
        tool_started: dict[int, bool] = {}
        tool_bufs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, str]] = {}

        yield StartEvent()

        async for chunk in await self._client.chat.completions.create(**params, stream=True):
            if self._cancelled():
                yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                return
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            delta = choice.delta

            if delta.content:
                if not text_started:
                    yield TextStartEvent(data=TextEventData(index=text_index))
                    text_started = True
                text_buf += delta.content
                yield TextDeltaEvent(data=TextEventData(index=text_index, text=delta.content))

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
                        yield ToolCallStartEvent(data=ToolCallEventData(
                            index=tool_index + idx,
                            id=tool_meta[idx]["id"],
                            name=tool_meta[idx]["name"],
                        ))
                    if tc.function and tc.function.arguments:
                        tool_bufs[idx] += tc.function.arguments
                        yield ToolCallDeltaEvent(data=ToolCallEventData(
                            index=tool_index + idx,
                            id=tool_meta[idx]["id"],
                            args=tc.function.arguments,
                        ))

            if choice.finish_reason:
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

                stop_reason = _STOP_REASON.get(choice.finish_reason, StopReason.Stop)
                yield DoneEvent(reason=stop_reason)

    async def invoke(self, messages: list[BaseMessage], model: str = "gpt-4o") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
