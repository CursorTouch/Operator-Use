"""
Google Antigravity API — SSE streaming via cloudcode-pa.googleapis.com.

Uses a Bearer token from Google OAuth to access Claude and Gemini models
through Google's Antigravity IDE quota.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, Optional

import httpx

from program.llm.api.base import BaseAPI
from program.llm.api.types import APIResponse
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
_LOAD_CODE_ASSIST_PATH = "/v1internal:loadCodeAssist"
_ONBOARD_USER_PATH = "/v1internal:onboardUser"
_ANTIGRAVITY_VERSION = "1.26.0"
_FALLBACK_PROJECT_ID = "rising-fact-p41fc"

_STOP_REASON: dict[str, StopReason] = {
    "STOP": StopReason.Stop,
    "MAX_TOKENS": StopReason.Length,
    "SAFETY": StopReason.ContentFilter,
    "RECITATION": StopReason.ContentFilter,
}


def _antigravity_headers(access_token: str) -> dict[str, str]:
    platform = "WINDOWS" if os.name == "nt" else "MACOS"
    arch = "windows/amd64" if os.name == "nt" else "darwin/arm64"
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/{_ANTIGRAVITY_VERSION} {arch}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {"ideType": "ANTIGRAVITY", "platform": platform, "pluginType": "GEMINI"}
        ),
        "accept": "text/event-stream",
    }


async def fetch_project_id(access_token: str, base_url: str = _DEFAULT_BASE_URL) -> str:
    """Discover the user's Cloud Code Assist project ID via loadCodeAssist."""
    headers = _antigravity_headers(access_token)
    body = {
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{base_url}{_LOAD_CODE_ASSIST_PATH}",
                json=body,
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                project = data.get("cloudaicompanionProject", "")
                if isinstance(project, str) and project:
                    return project
                if isinstance(project, dict) and project.get("id"):
                    return project["id"]
    except Exception:
        pass
    return _FALLBACK_PROJECT_ID


async def onboard_user(access_token: str, project_id: str, base_url: str = _DEFAULT_BASE_URL) -> None:
    """Activate the account for API access (safe to call repeatedly)."""
    headers = _antigravity_headers(access_token)
    body = {
        "cloudaicompanionProject": project_id,
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"{base_url}{_ONBOARD_USER_PATH}",
                json=body,
                headers=headers,
            )
    except Exception:
        pass


def _messages_to_contents(
    messages: list[BaseMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    system: str | None = None
    raw: list[dict[str, Any]] = []

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
                        parts.append({"inlineData": {"mimeType": "image/png", "data": b64}})
            if parts:
                raw.append({"role": "user", "parts": parts})
        elif isinstance(msg, AssistantMessage):
            parts = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append({"text": item.content})
                elif isinstance(item, ThinkingContent):
                    parts.append({"thought": True, "text": item.content})
                elif isinstance(item, ToolCallContent):
                    parts.append({
                        "functionCall": {
                            "name": item.name,
                            "args": item.args if isinstance(item.args, dict) else {},
                        }
                    })
            if parts:
                raw.append({"role": "model", "parts": parts})
        elif isinstance(msg, ToolMessage):
            text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
            raw.append({
                "role": "user",
                "parts": [{"functionResponse": {"name": msg.id, "response": {"result": text}}}],
            })

    # Merge consecutive same-role turns (Gemini requires strict alternation)
    contents: list[dict[str, Any]] = []
    for item in raw:
        if contents and contents[-1]["role"] == item["role"]:
            contents[-1]["parts"] = contents[-1]["parts"] + item["parts"]
        else:
            contents.append({"role": item["role"], "parts": list(item["parts"])})

    return system, contents


class GoogleAntigravityAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._base_url = (options.base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._project_id: Optional[str] = (options.headers or {}).get("x-goog-user-project")

    async def _ensure_project_id(self) -> str:
        if not self._project_id:
            self._project_id = await fetch_project_id(
                self.options.api_key or "", self._base_url
            )
        return self._project_id

    def _build_request_body(
        self,
        model: str,
        project: str,
        system: str | None,
        contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {}
        if self.options.temperature is not None:
            generation_config["temperature"] = self.options.temperature
        if self.options.max_tokens is not None:
            generation_config["maxOutputTokens"] = self.options.max_tokens
        if self.options.thinking_budget is not None:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": self.options.thinking_budget,
                "includeThoughts": True,
            }

        inner: dict[str, Any] = {"contents": contents}
        if system:
            inner["systemInstruction"] = {"parts": [{"text": system}]}
        if generation_config:
            inner["generationConfig"] = generation_config

        return {"model": model, "project": project, "request": inner}

    async def stream(self, messages: list[BaseMessage], model: str = "gemini-2.5-flash") -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        project = await self._ensure_project_id()
        system, contents = _messages_to_contents(messages)
        body = self._build_request_body(model, project, system, contents)
        headers = _antigravity_headers(self.options.api_key or "")

        if self.options.on_payload:
            modified = self.options.on_payload(body)
            if modified is not None:
                body = modified

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
                    if self.options.on_response:
                        self.options.on_response(APIResponse(response.status_code, dict(response.headers)))

                    if not response.is_success:
                        error_body = (await response.aread()).decode(errors="replace")
                        yield ErrorEvent(reason=StopReason.Abort, message=f"HTTP {response.status_code}: {error_body}")
                        return

                    async for line in response.aiter_lines():
                        if self._cancelled():
                            yield ErrorEvent(reason=StopReason.Abort, message="Cancelled")
                            return
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if not raw or raw == "[DONE]":
                            continue

                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # API wraps the response in a "response" key
                        if "response" in chunk:
                            chunk = chunk["response"]

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

        except Exception as exc:
            yield ErrorEvent(reason=StopReason.Abort, message=str(exc))
            return

        if thinking_started:
            yield ThinkingEndEvent(data=ThinkingEventData(index=thinking_index, thinking=thinking_buf))
        if text_started:
            yield TextEndEvent(data=TextEventData(index=text_index, text=text_buf))
        yield DoneEvent(reason=StopReason.Stop)

    async def invoke(self, messages: list[BaseMessage], model: str = "gemini-2.5-flash") -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(messages, model=model):
            events.append(event)
        return events
