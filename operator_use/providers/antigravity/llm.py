"""
Antigravity LLM provider.

Antigravity is Google's IDE that provides access to Gemini and Claude models
via Google's Cloud Code Assist API using OAuth credentials.

Supports:
- Gemini 3 models: gemini-3-pro, gemini-3-flash
- Gemini 2.5 models: gemini-2.5-pro, gemini-2.5-flash
- Claude models: claude-opus-4-6, claude-opus-4-6-thinking, claude-sonnet-4-6

Authentication: Run `operator auth antigravity`
"""

import json
import logging
import os
import time
from typing import AsyncIterator, Iterator, List, Optional

import httpx

from operator_use.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ImageMessage,
    SystemMessage,
    ToolMessage,
)
from operator_use.providers.base import BaseChatLLM
from operator_use.providers.events import (
    LLMEvent,
    LLMEventType,
    LLMStreamEvent,
    LLMStreamEventType,
    ToolCall,
    Thinking,
)
from operator_use.providers.views import Metadata, TokenUsage
from operator_use.tools import Tool
from operator_use.providers.antigravity.auth import (
    DEFAULT_PROJECT_ID,
    async_refresh_token,
    load_auth,
    refresh_token,
    save_auth,
)

logger = logging.getLogger(__name__)

# API endpoints (prod first, then sandbox fallback)
_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
]
_GENERATE = "/v1internal:generateContent"
_STREAM = "/v1internal:streamGenerateContent?alt=sse"

_ANTIGRAVITY_VERSION = "1.26.0"


def _antigravity_headers(access_token: str) -> dict:
    platform = "WINDOWS" if os.name == "nt" else "MACOS"
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/{_ANTIGRAVITY_VERSION} {'windows/amd64' if os.name == 'nt' else 'darwin/arm64'}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {"ideType": "ANTIGRAVITY", "platform": platform, "pluginType": "GEMINI"}
        ),
        "accept": "text/event-stream",
    }


# ---------------------------------------------------------------------------
# Message / tool conversion  (Google Generative AI format)
# ---------------------------------------------------------------------------


def _convert_messages(messages: List[BaseMessage]) -> tuple[Optional[str], list]:
    """Return (system_instruction_text, contents_list) in Gemini format."""
    system_instruction: Optional[str] = None
    raw_contents: list = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_instruction = msg.content
        elif isinstance(msg, HumanMessage):
            raw_contents.append({"role": "user", "parts": [{"text": msg.content}]})
        elif isinstance(msg, ImageMessage):
            parts = []
            if msg.content:
                parts.append({"text": msg.content})
            for b64 in msg.convert_images(format="base64"):
                parts.append({"inlineData": {"mimeType": msg.mime_type, "data": b64}})
            raw_contents.append({"role": "user", "parts": parts})
        elif isinstance(msg, AIMessage):
            parts = []
            if msg.thinking:
                parts.append({"thought": True, "text": msg.thinking})
            if msg.content:
                parts.append({"text": msg.content})
            if parts:
                raw_contents.append({"role": "model", "parts": parts})
        elif isinstance(msg, ToolMessage):
            model_parts = []
            if msg.thinking:
                model_parts.append({"thought": True, "text": msg.thinking})
            # functionCall part with thoughtSignature at part level (required by Gemini extended thinking API)
            fc_part = {"functionCall": {"name": msg.name, "args": msg.params}}
            if msg.thinking_signature:
                fc_part["thoughtSignature"] = msg.thinking_signature
            model_parts.append(fc_part)
            raw_contents.append({"role": "model", "parts": model_parts})
            raw_contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": msg.name,
                                "response": {"result": msg.content or ""},
                            }
                        }
                    ],
                }
            )

    # Merge consecutive same-role contents (Gemini requires strict alternation)
    contents: list = []
    for item in raw_contents:
        if contents and contents[-1]["role"] == item["role"]:
            contents[-1]["parts"] = contents[-1]["parts"] + item["parts"]
        else:
            contents.append({"role": item["role"], "parts": list(item["parts"])})

    return system_instruction, contents


_UNSUPPORTED_SCHEMA_KEYS = {
    "examples",
    "default",
    "additionalProperties",
    "$schema",
    "$defs",
    "const",
}


def _clean_schema(obj):
    """Recursively remove keys unsupported by the Gemini function calling schema."""
    if isinstance(obj, dict):
        return {k: _clean_schema(v) for k, v in obj.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(obj, list):
        return [_clean_schema(i) for i in obj]
    return obj


def _convert_tools(tools: List[Tool]) -> list:
    return [
        {
            "functionDeclarations": [
                {
                    "name": t.json_schema["name"],
                    "description": t.json_schema.get("description", ""),
                    "parameters": _clean_schema(t.json_schema.get("parameters", {})),
                }
                for t in tools
            ]
        }
    ]


def _build_body(
    model: str,
    project: str,
    system: Optional[str],
    contents: list,
    tools: list,
    generation_config: dict,
) -> dict:
    inner: dict = {"contents": contents}
    if system:
        inner["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        inner["tools"] = tools
        inner["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    if generation_config:
        inner["generationConfig"] = generation_config
    return {"model": model, "project": project, "request": inner}


# ---------------------------------------------------------------------------
# SSE response parsing
# ---------------------------------------------------------------------------


def _parse_sse_line(line: str) -> Optional[dict]:
    if line.startswith("data: "):
        data = line[6:].strip()
        if data and data != "[DONE]":
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
    return None


def _extract_from_chunk(
    chunk: dict,
) -> tuple[str, Optional[str], Optional[dict], Optional[str], Optional[dict]]:
    """Return (text, thinking_text, function_call, thought_signature, usage)."""
    # API wraps the response in a "response" key
    if "response" in chunk:
        chunk = chunk["response"]
    candidates = chunk.get("candidates", [])
    text = ""
    thinking = None
    thought_signature = None
    function_call = None

    for candidate in candidates:
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if part.get("thought"):
                thinking = (thinking or "") + part.get("text", "")
                if "thoughtSignature" in part and not thought_signature:
                    thought_signature = part.get("thoughtSignature")
            elif "text" in part:
                text += part["text"]
            elif "functionCall" in part:
                function_call = part["functionCall"]
                # thoughtSignature may be in functionCall part itself
                if "thoughtSignature" in part and not thought_signature:
                    thought_signature = part.get("thoughtSignature")

    usage_meta = chunk.get("usageMetadata")
    return text, thinking, function_call, thought_signature, usage_meta


def _make_usage(meta: Optional[dict]) -> Optional[TokenUsage]:
    if not meta:
        return None
    return TokenUsage(
        prompt_tokens=meta.get("promptTokenCount", 0),
        completion_tokens=meta.get("candidatesTokenCount", 0),
        total_tokens=meta.get("totalTokenCount", 0),
    )


def _extract_final(chunks: list[dict]) -> LLMEvent:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    function_call: Optional[dict] = None
    thought_signature: Optional[str] = None
    usage = None

    for chunk in chunks:
        t, th, fc, ts, meta = _extract_from_chunk(chunk)
        if t:
            text_parts.append(t)
        if th:
            thinking_parts.append(th)
        if fc:
            function_call = fc
        if ts and not thought_signature:
            thought_signature = ts
        if meta:
            usage = _make_usage(meta)

    if function_call:
        name = function_call.get("name", "")
        args = function_call.get("args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        # Generate a synthetic call ID
        import uuid

        call_id = f"call_{uuid.uuid4().hex[:8]}"
        return LLMEvent(
            type=LLMEventType.TOOL_CALL,
            tool_call=ToolCall(id=call_id, name=name, params=args),
            usage=usage,
            thinking=Thinking(
                content="".join(thinking_parts) if thinking_parts else "",
                signature=thought_signature,
            )
            if thinking_parts or thought_signature
            else None,
        )

    thinking_obj = None
    if thinking_parts or thought_signature:
        thinking_obj = Thinking(content="".join(thinking_parts), signature=thought_signature)

    return LLMEvent(
        type=LLMEventType.TEXT,
        content="".join(text_parts),
        thinking=thinking_obj,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# ChatAntigravity
# ---------------------------------------------------------------------------


class ChatAntigravity(BaseChatLLM):
    """
    LLM provider for Antigravity (Google Cloud Code Assist) via OAuth.

    Reads OAuth credentials from ~/.config/operator/antigravity_auth.json.
    Run `python -m operator_use.providers.antigravity.auth login` to authenticate.
    Falls back to ANTIGRAVITY_ACCESS_TOKEN env var.

    Supported models:
      - gemini-3-pro, gemini-3-flash
      - gemini-2.5-pro, gemini-2.5-flash
      - claude-opus-4-6, claude-opus-4-6-thinking, claude-sonnet-4-6
    """

    def __init__(
        self,
        model: str = "gemini-3-pro",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        self._model = model
        self._timeout = timeout
        self._temperature = temperature
        self._static_token = api_key or os.environ.get("ANTIGRAVITY_ACCESS_TOKEN")
        self._endpoint = base_url or _ENDPOINTS[0]
        self._auth: Optional[dict] = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._static_token:
            return self._static_token

        if self._auth is None:
            self._auth = load_auth()

        if self._auth is None:
            raise RuntimeError("No Antigravity credentials found. Run: operator auth antigravity")

        if self._auth.get("expires_at", 0) < time.time() + 60:
            logger.debug("Antigravity token expired, refreshing...")
            result = refresh_token(self._auth["refresh_token"])
            if result:
                self._auth.update(
                    {
                        "access_token": result["access_token"],
                        "expires_at": time.time() + result.get("expires_in", 3600),
                    }
                )
                save_auth(self._auth)
            else:
                raise RuntimeError(
                    "Failed to refresh Antigravity token. Run: operator auth antigravity"
                )

        return self._auth["access_token"]

    async def _async_get_token(self) -> str:
        if self._static_token:
            return self._static_token

        if self._auth is None:
            self._auth = load_auth()

        if self._auth is None:
            raise RuntimeError("No Antigravity credentials found. Run: operator auth antigravity")

        if self._auth.get("expires_at", 0) < time.time() + 60:
            logger.debug("Antigravity token expired, refreshing async...")
            result = await async_refresh_token(self._auth["refresh_token"])
            if result:
                self._auth.update(
                    {
                        "access_token": result["access_token"],
                        "expires_at": time.time() + result.get("expires_in", 3600),
                    }
                )
                save_auth(self._auth)
            else:
                raise RuntimeError(
                    "Failed to refresh Antigravity token. Run: operator auth antigravity"
                )

        return self._auth["access_token"]

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _prepare(self, messages: List[BaseMessage], tools: List[Tool]) -> tuple[dict, dict]:
        """Return (headers, body)."""
        token = self._get_token()
        headers = _antigravity_headers(token)
        system, contents = _convert_messages(messages)
        gen_cfg: dict = {}
        if self._temperature is not None:
            gen_cfg["temperature"] = self._temperature
        project = (
            (self._auth or {}).get("project_id", DEFAULT_PROJECT_ID)
            if not self._static_token
            else DEFAULT_PROJECT_ID
        )
        body = _build_body(
            self._model, project, system, contents, _convert_tools(tools) if tools else [], gen_cfg
        )
        return headers, body

    async def _async_prepare(
        self, messages: List[BaseMessage], tools: List[Tool]
    ) -> tuple[dict, dict]:
        token = await self._async_get_token()
        headers = _antigravity_headers(token)
        system, contents = _convert_messages(messages)
        gen_cfg: dict = {}
        if self._temperature is not None:
            gen_cfg["temperature"] = self._temperature
        project = (
            (self._auth or {}).get("project_id", DEFAULT_PROJECT_ID)
            if not self._static_token
            else DEFAULT_PROJECT_ID
        )
        body = _build_body(
            self._model, project, system, contents, _convert_tools(tools) if tools else [], gen_cfg
        )
        return headers, body

    # ------------------------------------------------------------------
    # BaseChatLLM interface
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "antigravity"

    def invoke(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> LLMEvent:
        headers, body = self._prepare(messages, tools)
        chunks: list[dict] = []

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", self._endpoint + _STREAM, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    r.read()
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} {r.reason_phrase}: {r.text}",
                        request=r.request,
                        response=r,
                    )
                for line in r.iter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk:
                        chunks.append(chunk)

        return _extract_final(chunks)

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> LLMEvent:
        headers, body = await self._async_prepare(messages, tools)
        chunks: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._endpoint + _STREAM, headers=headers, json=body
            ) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} {r.reason_phrase}: {r.text}",
                        request=r.request,
                        response=r,
                    )
                async for line in r.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk:
                        chunks.append(chunk)

        return _extract_final(chunks)

    def stream(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> Iterator[LLMStreamEvent]:
        headers, body = self._prepare(messages, tools)

        text_started = False
        think_started = False
        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args: dict = {}
        thought_signature: Optional[str] = None
        usage = None

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", self._endpoint + _STREAM, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    logger.error(
                        f"Antigravity stream 400+ error: {r.status_code}\n{r.read().decode()}"
                    )
                for line in r.iter_lines():
                    chunk = _parse_sse_line(line)
                    if not chunk:
                        continue

                    t, th, fc, ts, meta = _extract_from_chunk(chunk)
                    if meta:
                        usage = _make_usage(meta)

                    if th:
                        if not think_started:
                            think_started = True
                            yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                        yield LLMStreamEvent(type=LLMStreamEventType.THINK_DELTA, content=th)

                    if ts and not thought_signature:
                        thought_signature = ts

                    if t:
                        if think_started:
                            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                            think_started = False
                        if not text_started:
                            text_started = True
                            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                        yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=t)

                    if fc:
                        tool_name = fc.get("name")
                        tool_args = fc.get("args", {})
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}
                        import uuid

                        tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

        if think_started:
            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
        if text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)
        if tool_name and tool_call_id:
            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_name, params=tool_args),
                usage=usage,
                thinking=Thinking(content="", signature=thought_signature)
                if thought_signature
                else None,
            )

    async def astream(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        headers, body = await self._async_prepare(messages, tools)

        text_started = False
        think_started = False
        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args: dict = {}
        thought_signature: Optional[str] = None
        usage = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._endpoint + _STREAM, headers=headers, json=body
            ) as r:
                if r.status_code >= 400:
                    logger.error(
                        f"Antigravity astream 400+ error: {r.status_code}\n{(await r.aread()).decode()}"
                    )
                async for line in r.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if not chunk:
                        continue

                    t, th, fc, ts, meta = _extract_from_chunk(chunk)
                    if meta:
                        usage = _make_usage(meta)

                    if th:
                        if not think_started:
                            think_started = True
                            yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                        yield LLMStreamEvent(type=LLMStreamEventType.THINK_DELTA, content=th)

                    if ts and not thought_signature:
                        thought_signature = ts

                    if t:
                        if think_started:
                            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                            think_started = False
                        if not text_started:
                            text_started = True
                            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                        yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=t)

                    if fc:
                        tool_name = fc.get("name")
                        tool_args = fc.get("args", {})
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}
                        import uuid

                        tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

        if think_started:
            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
        if text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)
        if tool_name and tool_call_id:
            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_name, params=tool_args),
                usage=usage,
                thinking=Thinking(content="", signature=thought_signature)
                if thought_signature
                else None,
            )

    def get_metadata(self) -> Metadata:
        context_windows = {
            "gemini-3-pro": 1_000_000,
            "gemini-3-flash": 1_000_000,
            "gemini-2.5-pro": 1_000_000,
            "gemini-2.5-flash": 1_000_000,
            "claude-opus-4-6": 200_000,
            "claude-opus-4-6-thinking": 200_000,
            "claude-sonnet-4-6": 200_000,
        }
        return Metadata(
            name=self._model,
            context_window=context_windows.get(self._model, 200_000),
            owned_by="google-antigravity",
        )
