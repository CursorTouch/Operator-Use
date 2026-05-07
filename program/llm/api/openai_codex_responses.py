from __future__ import annotations
import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets
import websockets.asyncio.client

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

_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
_JWT_CLAIM_PATH = "https://api.openai.com/auth"
_MAX_RETRIES = 3
_BASE_DELAY_S = 1.0
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRYABLE_RE = re.compile(
    r"rate.?limit|overloaded|service.?unavailable|upstream.?connect|connection.?refused", re.I
)
_COMPLETION_TYPES = {"response.done", "response.completed", "response.incomplete"}

_THINKING_EFFORT: dict[ThinkingLevel, str] = {
    ThinkingLevel.Low: "low",
    ThinkingLevel.Minimal: "low",
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


# ── Auth ──────────────────────────────────────────────────────────────────────

def _extract_account_id(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a JWT")
        padding = (4 - len(parts[1]) % 4) % 4
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * padding))
        account_id = payload.get(_JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
        if not account_id:
            raise ValueError("missing chatgpt_account_id")
        return account_id
    except Exception as exc:
        raise ValueError(f"Failed to extract account_id from token: {exc}") from exc


# ── URL ───────────────────────────────────────────────────────────────────────

def _resolve_http_url(base_url: str | None) -> str:
    raw = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    if raw.endswith("/codex/responses"):
        return raw
    if raw.endswith("/codex"):
        return f"{raw}/responses"
    return f"{raw}/codex/responses"


def _resolve_ws_url(base_url: str | None) -> str:
    http = _resolve_http_url(base_url)
    return http.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


# ── Message → input conversion ────────────────────────────────────────────────

def _content_to_input(content_items: list) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for item in content_items:
        if isinstance(item, TextContent):
            parts.append({"type": "input_text", "text": item.content})
        elif isinstance(item, ImageContent):
            for b64 in item.to_base64():
                url = b64 if b64.startswith("http") else f"data:image/png;base64,{b64}"
                parts.append({"type": "input_image", "image_url": url})
        elif isinstance(item, ThinkingContent):
            parts.append({"type": "thinking", "thinking": item.content, "signature": item.signature})
        elif isinstance(item, ToolCallContent):
            parts.append({
                "type": "function_call",
                "call_id": item.id,
                "name": item.name,
                "arguments": json.dumps(item.args),
            })
    return parts


def _messages_to_input(messages: list[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
    instructions = "You are a helpful assistant."
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            if text:
                instructions = text
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
            parts = _content_to_input(msg.contents)
            if parts:
                input_items.append({"role": role, "content": parts})

    return instructions, input_items


# ── Request building ──────────────────────────────────────────────────────────

def _build_body(
    model: str,
    instructions: str,
    input_items: list[dict[str, Any]],
    options: Options,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": instructions,
        "input": input_items,
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if options.temperature is not None:
        body["temperature"] = options.temperature
    if options.max_tokens is not None:
        body["max_output_tokens"] = options.max_tokens
    if options.thinking_level is not None:
        body["reasoning"] = {
            "effort": _THINKING_EFFORT[options.thinking_level],
            "summary": "auto",
        }
    return body


def _build_headers(token: str, account_id: str, *, websocket: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "originator": "pi",
    }
    if websocket:
        headers["OpenAI-Beta"] = "responses_websockets=2026-02-06"
    else:
        headers["OpenAI-Beta"] = "responses=experimental"
        headers["accept"] = "text/event-stream"
        headers["content-type"] = "application/json"
    return headers


# ── Codex event normalization ─────────────────────────────────────────────────

async def _map_codex_events(
    raw: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    async for event in raw:
        etype = event.get("type", "")

        if etype == "error":
            code = event.get("code", "")
            message = event.get("message", "") or code or json.dumps(event)
            raise RuntimeError(f"Codex error: {message}")

        if etype == "response.failed":
            err = (event.get("response") or {}).get("error") or {}
            raise RuntimeError(err.get("message") or "Codex response failed")

        if etype in _COMPLETION_TYPES:
            yield {**event, "type": "response.completed", "response": event.get("response") or {}}
            return

        yield event


# ── SSE parsing ───────────────────────────────────────────────────────────────

async def _parse_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            data_lines = [
                line[5:].strip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            data = "\n".join(data_lines).strip()
            if not data or data == "[DONE]":
                continue
            yield json.loads(data)


# ── WebSocket parsing ─────────────────────────────────────────────────────────

async def _parse_ws(ws: websockets.asyncio.client.ClientConnection) -> AsyncIterator[dict[str, Any]]:
    saw_completion = False
    async for raw in ws:
        event: dict[str, Any] = json.loads(raw)
        etype = event.get("type", "")
        if etype in _COMPLETION_TYPES:
            saw_completion = True
        yield event
        if saw_completion:
            return
    if not saw_completion:
        raise RuntimeError("WebSocket stream closed before completion event")


# ── Retry helper ──────────────────────────────────────────────────────────────

def _is_retryable(status: int, body: str) -> bool:
    return status in _RETRYABLE_STATUSES or bool(_RETRYABLE_RE.search(body))


# ── Event processing ──────────────────────────────────────────────────────────

async def _process_events(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[LLMEvent]:
    text_index = 0
    thinking_index = 0
    tool_index = 0
    tool_names: dict[str, str] = {}

    async for event in events:
        etype = event.get("type", "")

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            itype = item.get("type", "")
            if itype == "message":
                yield TextStartEvent(data=TextEventData(index=text_index))
            elif itype == "reasoning":
                yield ThinkingStartEvent(data=ThinkingEventData(index=thinking_index))
            elif itype == "function_call":
                call_id = item.get("call_id", "")
                name = item.get("name", "")
                tool_names[call_id] = name
                yield ToolCallStartEvent(data=ToolCallEventData(
                    index=tool_index, id=call_id, name=name,
                ))

        elif etype == "response.output_text.delta":
            yield TextDeltaEvent(data=TextEventData(index=text_index, text=event.get("delta", "")))

        elif etype == "response.output_text.done":
            yield TextEndEvent(data=TextEventData(index=text_index, text=event.get("text", "")))
            text_index += 1

        elif etype == "response.reasoning_summary_text.delta":
            yield ThinkingDeltaEvent(data=ThinkingEventData(index=thinking_index, thinking=event.get("delta", "")))

        elif etype == "response.reasoning_summary_text.done":
            yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=event.get("text", "")))
            thinking_index += 1

        elif etype == "response.function_call_arguments.delta":
            item_id = event.get("item_id", "")
            yield ToolCallDeltaEvent(data=ToolCallEventData(
                index=tool_index, id=item_id, args=event.get("delta", ""),
            ))

        elif etype == "response.function_call_arguments.done":
            item_id = event.get("item_id", "")
            yield ToolCallEndEvent(data=ToolCallEventData(
                index=tool_index,
                id=item_id,
                name=tool_names.get(item_id, ""),
                args=event.get("arguments", ""),
            ))
            tool_index += 1

        elif etype == "response.completed":
            response = event.get("response") or {}
            stop_reason = _STOP_REASON.get(response.get("stop_reason") or "", StopReason.Stop)
            yield DoneEvent(reason=stop_reason)


# ── API class ─────────────────────────────────────────────────────────────────

class OpenAICodexResponsesAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._http_url = _resolve_http_url(options.base_url)
        self._ws_url = _resolve_ws_url(options.base_url)
        self._http_client = httpx.AsyncClient(
            timeout=options.timeout.total_seconds(),
            headers=options.headers or {},
        )

    async def _stream_sse(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[LLMEvent]:
        body_bytes = json.dumps(body).encode()
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
            try:
                async with self._http_client.stream(
                    "POST", self._http_url, content=body_bytes, headers=headers,
                ) as response:
                    if not response.is_success:
                        text = (await response.aread()).decode(errors="replace")
                        if attempt < _MAX_RETRIES and _is_retryable(response.status_code, text):
                            last_error = RuntimeError(f"HTTP {response.status_code}: {text}")
                            continue
                        raise RuntimeError(f"HTTP {response.status_code}: {text}")

                    async for event in _process_events(_map_codex_events(_parse_sse(response))):
                        yield event
                    return

            except RuntimeError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < _MAX_RETRIES:
                    continue
                raise

        raise last_error or RuntimeError("Failed after retries")

    async def _stream_ws(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[LLMEvent]:
        ws_headers = {k: v for k, v in headers.items() if k.lower() not in ("accept", "content-type")}
        async with websockets.asyncio.client.connect(
            self._ws_url,
            additional_headers=ws_headers,
        ) as ws:
            await ws.send(json.dumps({"type": "response.create", **body}))
            async for event in _process_events(_map_codex_events(_parse_ws(ws))):
                yield event

    async def stream(self, messages: list[BaseMessage], model: str = "gpt-4o") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        token = self.options.api_key or ""
        account_id = _extract_account_id(token)
        instructions, input_items = _messages_to_input(messages)
        body = _build_body(model, instructions, input_items, self.options)

        yield StartEvent()

        # Try WebSocket first for lower latency; fall back to SSE on failure.
        try:
            ws_headers = _build_headers(token, account_id, websocket=True)
            async for event in self._stream_ws(body, ws_headers):
                yield event
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                    return
        except Exception:
            sse_headers = _build_headers(token, account_id, websocket=False)
            async for event in self._stream_sse(body, sse_headers):
                yield event
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                    return

    async def invoke(self, messages: list[BaseMessage], model: str = "gpt-4o") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
