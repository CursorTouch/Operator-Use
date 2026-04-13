"""
GitHub Copilot LLM provider.

Uses the GitHub Copilot Chat Completions API (OpenAI-compatible format)
with OAuth credentials from the GitHub Device Flow.

Token resolution order:
  1. api_key argument (direct Copilot token)
  2. GITHUB_COPILOT_TOKEN environment variable
  3. GITHUB_TOKEN / GH_TOKEN environment variable (exchanges for Copilot token)
  4. ~/.config/operator/github_copilot_auth.json (written by `operator auth github-copilot`)
"""

import json
import logging
import os
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
)
from operator_use.providers.views import Metadata, TokenUsage
from operator_use.tools import Tool

logger = logging.getLogger(__name__)

COPILOT_BASE_URL = "https://api.githubcopilot.com"
CHAT_PATH = "/chat/completions"


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def _convert_messages(messages: List[BaseMessage]) -> list:
    result = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, ImageMessage):
            parts = []
            if msg.content:
                parts.append({"type": "text", "text": msg.content})
            for b64 in msg.convert_images(format="base64"):
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{msg.mime_type};base64,{b64}"},
                    }
                )
            result.append({"role": "user", "content": parts})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": msg.content or ""})
        elif isinstance(msg, ToolMessage):
            result.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": msg.id,
                            "type": "function",
                            "function": {"name": msg.name, "arguments": json.dumps(msg.params)},
                        }
                    ],
                }
            )
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.id,
                    "content": msg.content or "",
                }
            )
    return result


def _convert_tools(tools: List[Tool]) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t.json_schema["name"],
                "description": t.json_schema.get("description", ""),
                "parameters": t.json_schema.get("parameters", {}),
            },
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


def _parse_sse(line: str) -> Optional[dict]:
    if line.startswith("data: "):
        data = line[6:].strip()
        if data and data != "[DONE]":
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
    return None


def _extract_final(chunks: list[dict]) -> LLMEvent:
    text_parts: list[str] = []
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_args_parts: list[str] = []
    usage = None

    for chunk in chunks:
        choices = chunk.get("choices", [])
        for choice in choices:
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                text_parts.append(content)
            tool_calls = delta.get("tool_calls", [])
            for tc in tool_calls:
                if tc.get("id"):
                    tool_call_id = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_name = fn["name"]
                if fn.get("arguments"):
                    tool_args_parts.append(fn["arguments"])
        if chunk.get("usage"):
            u = chunk["usage"]
            usage = TokenUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

    if tool_name and tool_call_id:
        args_str = "".join(tool_args_parts)
        try:
            params = json.loads(args_str)
        except json.JSONDecodeError:
            params = {}
        return LLMEvent(
            type=LLMEventType.TOOL_CALL,
            tool_call=ToolCall(id=tool_call_id, name=tool_name, params=params),
            usage=usage,
        )

    return LLMEvent(type=LLMEventType.TEXT, content="".join(text_parts), usage=usage)


# ---------------------------------------------------------------------------
# ChatGitHubCopilot
# ---------------------------------------------------------------------------


class ChatGitHubCopilot(BaseChatLLM):
    """
    LLM provider for GitHub Copilot via the Chat Completions API.

    Authenticates using GitHub OAuth (Device Flow). The short-lived Copilot
    API token (valid 25 min) is auto-refreshed from the stored GitHub token.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        self._model = model
        self._timeout = timeout
        self._temperature = temperature
        self._base_url = (base_url or COPILOT_BASE_URL).rstrip("/")
        # Static token overrides OAuth flow
        self._static_token = api_key or os.environ.get("GITHUB_COPILOT_TOKEN")
        # GitHub token for exchanging to Copilot token
        self._env_github_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self._auth: Optional[dict] = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._static_token:
            return self._static_token

        from operator_use.providers.github_copilot.auth import (
            load_auth,
            get_copilot_token,
            _exchange_copilot_token,
        )

        # Try env GitHub token first (no stored auth needed)
        if self._env_github_token and self._auth is None:
            try:
                copilot = _exchange_copilot_token(self._env_github_token)
                self._auth = {
                    "github_token": self._env_github_token,
                    "copilot_token": copilot["token"],
                    "copilot_expires_at": copilot["expires_at"],
                }
            except Exception as e:
                logger.warning(f"Failed to exchange env GitHub token: {e}")

        if self._auth is None:
            self._auth = load_auth()

        if self._auth is None:
            raise RuntimeError(
                "No GitHub Copilot credentials found. "
                "Run `operator auth github-copilot` to authenticate, "
                "or set GITHUB_TOKEN / GH_TOKEN."
            )

        return get_copilot_token(self._auth)

    async def _async_get_token(self) -> str:
        if self._static_token:
            return self._static_token

        from operator_use.providers.github_copilot.auth import (
            load_auth,
            async_get_copilot_token,
            _async_exchange_copilot_token,
        )

        if self._env_github_token and self._auth is None:
            try:
                copilot = await _async_exchange_copilot_token(self._env_github_token)
                self._auth = {
                    "github_token": self._env_github_token,
                    "copilot_token": copilot["token"],
                    "copilot_expires_at": copilot["expires_at"],
                }
            except Exception as e:
                logger.warning(f"Failed to exchange env GitHub token: {e}")

        if self._auth is None:
            self._auth = load_auth()

        if self._auth is None:
            raise RuntimeError(
                "No GitHub Copilot credentials found. "
                "Run `operator auth github-copilot` to authenticate, "
                "or set GITHUB_TOKEN / GH_TOKEN."
            )

        return await async_get_copilot_token(self._auth)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.95.0",
            "Accept": "text/event-stream",
        }

    def _body(self, messages: List[BaseMessage], tools: List[Tool], stream: bool) -> dict:
        body: dict = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "stream": stream,
            "stream_options": {"include_usage": True} if stream else None,
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if tools:
            body["tools"] = _convert_tools(tools)
            body["tool_choice"] = "auto"
        if not stream:
            body.pop("stream_options", None)
        return {k: v for k, v in body.items() if v is not None}

    # ------------------------------------------------------------------
    # BaseChatLLM interface
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "github_copilot"

    def invoke(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> LLMEvent:
        token = self._get_token()
        url = self._base_url + CHAT_PATH
        chunks: list[dict] = []

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream(
                "POST",
                url,
                headers=self._headers(token),
                json=self._body(messages, tools, stream=True),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    chunk = _parse_sse(line)
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
        token = await self._async_get_token()
        url = self._base_url + CHAT_PATH
        chunks: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(token),
                json=self._body(messages, tools, stream=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    chunk = _parse_sse(line)
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
        token = self._get_token()
        url = self._base_url + CHAT_PATH

        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args = ""
        text_started = False
        usage = None

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream(
                "POST",
                url,
                headers=self._headers(token),
                json=self._body(messages, tools, stream=True),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    chunk = _parse_sse(line)
                    if not chunk:
                        continue
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.TEXT_DELTA, content=content
                            )
                        for tc in delta.get("tool_calls", []):
                            if tc.get("id"):
                                tool_call_id = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_name = fn["name"]
                            if fn.get("arguments"):
                                tool_args += fn["arguments"]
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        usage = TokenUsage(
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                        )

        if text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)

        if tool_name and tool_call_id:
            try:
                params = json.loads(tool_args)
            except json.JSONDecodeError:
                params = {}
            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_name, params=params),
                usage=usage,
            )

    async def astream(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        token = await self._async_get_token()
        url = self._base_url + CHAT_PATH

        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args = ""
        text_started = False
        usage = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(token),
                json=self._body(messages, tools, stream=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    chunk = _parse_sse(line)
                    if not chunk:
                        continue
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.TEXT_DELTA, content=content
                            )
                        for tc in delta.get("tool_calls", []):
                            if tc.get("id"):
                                tool_call_id = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_name = fn["name"]
                            if fn.get("arguments"):
                                tool_args += fn["arguments"]
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        usage = TokenUsage(
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                        )

        if text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)

        if tool_name and tool_call_id:
            try:
                params = json.loads(tool_args)
            except json.JSONDecodeError:
                params = {}
            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_name, params=params),
                usage=usage,
            )

    def get_metadata(self) -> Metadata:
        return Metadata(name=self._model, context_window=128_000, owned_by="github")
