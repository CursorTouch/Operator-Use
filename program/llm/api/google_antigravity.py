"""
Google Antigravity API — SSE streaming via cloudcode-pa.googleapis.com.

Uses a Bearer token from Google OAuth to access Claude and Gemini models
through Google's Antigravity IDE quota.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from program.llm.api.base import BaseAPI
from program.llm.types import (
    LLMEvent, Options, StopReason,
    StartEvent, DoneEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent, TextEventData,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent, ThinkingEventData,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallEventData,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent,
)

__all__ = ["GoogleAntigravityAPI"]

_DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com"
_STREAM_PATH = "/v1internal:streamGenerateContent?alt=sse"
_DEFAULT_PROJECT_ID = "rising-fact-p41fc"

_STOP_REASON: dict[str, StopReason] = {
    "STOP": StopReason.Stop,
    "MAX_TOKENS": StopReason.Length,
    "SAFETY": StopReason.ContentFilter,
    "RECITATION": StopReason.ContentFilter,
}


def _messages_to_contents(
    messages: list[BaseMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    system: str | None = None
    contents: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
        elif isinstance(msg, UserMessage):
            parts: list[dict[str, Any]] = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append({"text": item.content})
                elif isinstance(item, ImageContent):
                    for b64 in item.to_base64():
                        parts.append({
                            "inlineData": {"mimeType": "image/png", "data": b64},
                        })
            if parts:
                contents.append({"role": "user", "parts": parts})
        elif isinstance(msg, AssistantMessage):
            parts = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append({"text": item.content})
                elif isinstance(item, ToolCallContent):
                    parts.append({
                        "functionCall": {
                            "name": item.name,
                            "args": item.args if isinstance(item.args, dict) else {},
                        }
                    })
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif isinstance(msg, ToolMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            contents.append({
                "role": "user",
                "parts": [{"functionResponse": {"name": msg.id, "response": {"result": text}}}],
            })

    return system, contents


class GoogleAntigravityAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._base_url = (options.base_url or _DEFAULT_BASE_URL).rstrip("/")
        headers = dict(options.headers or {})
        headers.setdefault("x-goog-user-project", _DEFAULT_PROJECT_ID)
        self._headers = headers

    def _build_request_body(
        self,
        model: str,
        system: str | None,
        contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            generation_config["maxOutputTokens"] = self.options.max_tokens
        if self.options.thinking_budget is not None:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": self.options.thinking_budget,
                "includeThoughts": True,
            }

        inner: dict[str, Any] = {
            "model": model,
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system:
            inner["systemInstruction"] = {"parts": [{"text": system}]}

        return {"model": model, "request": inner}

    async def stream(self, messages: list[BaseMessage], model: str = "claude-sonnet-4-6") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        system, contents = _messages_to_contents(messages)
        body = self._build_request_body(model, system, contents)

        headers = {
            **self._headers,
            "Authorization": f"Bearer {self.options.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        text_index = 0
        thinking_index = 0
        tool_index = 0
        text_started = False
        thinking_started = False
        text_buf = ""
        thinking_buf = ""

        yield StartEvent()

        try:
            async with httpx.AsyncClient(timeout=self.options.timeout.total_seconds()) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}{_STREAM_PATH}",
                    headers=headers,
                    content=json.dumps(body),
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if not raw or raw == "[DONE]":
                            continue

                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        candidates = chunk.get("candidates", [])
                        if not candidates:
                            continue

                        candidate = candidates[0]
                        content = candidate.get("content", {})
                        for part in content.get("parts", []):
                            if part.get("thought") and part.get("text"):
                                if not thinking_started:
                                    yield ThinkingStartEvent(data=ThinkingEventData(index=thinking_index))
                                    thinking_started = True
                                delta = part["text"]
                                thinking_buf += delta
                                yield ThinkingDeltaEvent(data=ThinkingEventData(index=thinking_index, thinking=delta))
                            elif part.get("text"):
                                if thinking_started:
                                    yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
                                    thinking_started = False
                                    thinking_index += 1
                                    thinking_buf = ""
                                if not text_started:
                                    yield TextStartEvent(data=TextEventData(index=text_index))
                                    text_started = True
                                delta = part["text"]
                                text_buf += delta
                                yield TextDeltaEvent(data=TextEventData(index=text_index, text=delta))
                            elif part.get("functionCall"):
                                fc = part["functionCall"]
                                name = fc.get("name", "")
                                args_str = json.dumps(fc.get("args", {}))
                                yield ToolCallStartEvent(data=ToolCallEventData(index=tool_index, id=name, name=name))
                                yield ToolCallDeltaEvent(data=ToolCallEventData(index=tool_index, id=name, args=args_str))
                                yield ToolCallEndEvent(data=ToolCallEventData(index=tool_index, id=name, name=name, args=args_str))
                                tool_index += 1

                        finish_reason = candidate.get("finishReason", "")
                        if finish_reason and finish_reason not in ("", "FINISH_REASON_UNSPECIFIED"):
                            if thinking_started:
                                yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
                                thinking_index += 1
                            if text_started:
                                yield TextEndEvent(data=TextEventData(index=text_index, text=text_buf))
                                text_index += 1
                            yield DoneEvent(reason=_STOP_REASON.get(finish_reason, StopReason.Stop))
                            return

        except httpx.HTTPStatusError as exc:
            yield ErrorEvent(reason=StopReason.Abort, message=f"HTTP {exc.response.status_code}: {exc.response.text}")
            return
        except Exception as exc:
            yield ErrorEvent(reason=StopReason.Abort, message=str(exc))
            return

        if thinking_started:
            yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
        if text_started:
            yield TextEndEvent(data=TextEventData(index=text_index, text=text_buf))
        yield DoneEvent(reason=StopReason.Stop)

    async def invoke(self, messages: list[BaseMessage], model: str = "claude-sonnet-4-6") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
