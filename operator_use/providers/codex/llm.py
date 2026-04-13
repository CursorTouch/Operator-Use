"""
OpenAI Codex provider via ChatGPT subscription OAuth.

Uses the ChatGPT backend API (chatgpt.com/backend-api/codex/responses)
with an OAuth token obtained by the Codex CLI (`codex login`).
Tokens are read from ~/.codex/auth.json and auto-refreshed when expired.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
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

CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_PATH = "/codex/responses"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _resolve_codex_home() -> Path:
    """Resolve ~/.codex directory, respecting CODEX_HOME env var."""
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _load_auth() -> Optional[dict]:
    """
    Load Codex CLI credentials from ~/.codex/auth.json.

    File format (from OpenClaw cli-credentials.ts):
      {"tokens": {"access_token": "...", "refresh_token": "...", "account_id": "..."}}

    We store our own runtime cache in the same file with extra fields:
      {"tokens": {...}, "_operator_expires": <epoch_seconds>}
    """
    auth_path = _resolve_codex_home() / "auth.json"
    if not auth_path.exists():
        return None
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        tokens = data.get("tokens", {})
        if isinstance(tokens, dict) and tokens.get("access_token"):
            # Derive expiry: file mtime + 1h (same as OpenClaw) unless we stored our own
            expires = data.get("_operator_expires")
            if not expires:
                expires = auth_path.stat().st_mtime + 3600
            return {
                "access": tokens["access_token"],
                "refresh": tokens.get("refresh_token", ""),
                "account_id": tokens.get("account_id", ""),
                "expires": expires,
                "_path": str(auth_path),
            }
    except Exception as e:
        logger.warning(f"Cannot read Codex auth from {auth_path}: {e}")
    return None


def _save_auth(data: dict) -> None:
    """Persist updated expiry back into auth.json without touching the tokens block."""
    path_str = data.get("_path")
    if not path_str:
        return
    auth_path = Path(path_str)
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
        raw["_operator_expires"] = data["expires"]
        auth_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Cannot save Codex auth to {auth_path}: {e}")


def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (4 - len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _account_id(access_token: str) -> str:
    payload = _jwt_payload(access_token)
    auth_claim = payload.get("https://api.openai.com/auth", {})
    if isinstance(auth_claim, dict):
        return auth_claim.get("chatgpt_account_id") or auth_claim.get("account_id") or ""
    return ""


def _do_refresh(refresh_token: str) -> Optional[dict]:
    try:
        r = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if r.status_code == 200:
            d = r.json()
            if d.get("access_token") and d.get("refresh_token"):
                return d
    except Exception as e:
        logger.error(f"Codex token refresh failed: {e}")
    return None


async def _async_refresh(refresh_token: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CLIENT_ID,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("access_token") and d.get("refresh_token"):
                    return d
    except Exception as e:
        logger.error(f"Codex async token refresh failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Message / tool conversion
# ---------------------------------------------------------------------------


def _convert_messages(messages: List[BaseMessage]) -> tuple[Optional[str], list]:
    """Return (instructions, input_items) for Responses API."""
    instructions = None
    items: list = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            instructions = msg.content
        elif isinstance(msg, HumanMessage):
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": msg.content}],
                }
            )
        elif isinstance(msg, ImageMessage):
            content_parts = []
            if msg.content:
                content_parts.append({"type": "input_text", "text": msg.content})
            for b64 in msg.convert_images(format="base64"):
                content_parts.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{msg.mime_type};base64,{b64}",
                    }
                )
            items.append({"type": "message", "role": "user", "content": content_parts})
        elif isinstance(msg, AIMessage):
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": msg.content or ""}],
                }
            )
        elif isinstance(msg, ToolMessage):
            # Emit the function_call item then the function_call_output item
            items.append(
                {
                    "type": "function_call",
                    "call_id": msg.id,
                    "name": msg.name,
                    "arguments": json.dumps(msg.params),
                }
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.id,
                    "output": msg.content or "",
                }
            )

    return instructions, items


_UNSUPPORTED_SCHEMA_KEYS = {
    "examples",
    "default",
    "additionalProperties",
    "$schema",
    "$defs",
    "title",
}


def _sanitize_schema(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_schema(v) for k, v in obj.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(obj, list):
        return [_sanitize_schema(i) for i in obj]
    return obj


def _convert_tools(tools: List[Tool]) -> list:
    return [
        {
            "type": "function",
            "name": t.json_schema["name"],
            "description": t.json_schema.get("description", ""),
            "parameters": _sanitize_schema(t.json_schema.get("parameters", {})),
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# SSE parsing helpers
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


def _extract_event(
    event: dict,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return (event_type, text_delta, tool_name, tool_args_delta)."""
    etype = event.get("type", "")

    if etype == "response.output_text.delta":
        return "text_delta", event.get("delta", ""), None, None

    if etype == "response.output_item.added":
        item = event.get("item", {})
        if item.get("type") == "function_call":
            return "tool_start", None, item.get("name"), None

    if etype == "response.function_call_arguments.delta":
        return "tool_delta", None, None, event.get("delta", "")

    if etype == "response.completed":
        return "done", None, None, None

    return None, None, None, None


def _extract_final(events: list[dict]) -> LLMEvent:
    """Build LLMEvent from accumulated SSE events."""
    text_parts: list[str] = []
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_args_parts: list[str] = []
    usage = None

    for event in events:
        etype = event.get("type", "")

        if etype == "response.output_text.delta":
            text_parts.append(event.get("delta", ""))

        elif etype == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                tool_name = item.get("name")
                tool_call_id = item.get("call_id") or item.get("id")

        elif etype == "response.function_call_arguments.delta":
            tool_args_parts.append(event.get("delta", ""))

        elif etype == "response.completed":
            resp = event.get("response", {})
            usage_data = resp.get("usage", {})
            if usage_data:
                usage = TokenUsage(
                    prompt_tokens=usage_data.get("input_tokens", 0),
                    completion_tokens=usage_data.get("output_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
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

    return LLMEvent(
        type=LLMEventType.TEXT,
        content="".join(text_parts),
        usage=usage,
    )


# ---------------------------------------------------------------------------
# ChatCodex
# ---------------------------------------------------------------------------


class ChatCodex(BaseChatLLM):
    """
    LLM provider for OpenAI Codex via ChatGPT subscription (OAuth).

    Reads OAuth credentials from ~/.codex/auth.json (written by `codex login`).
    Falls back to CODEX_API_KEY env var for direct API key usage.
    """

    def __init__(
        self,
        model: str = "gpt-5.4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        **kwargs,
    ):
        self._model = model
        self._timeout = timeout
        self._base_url = base_url or CODEX_BASE_URL
        self._static_api_key = api_key or os.environ.get("CODEX_API_KEY")
        self._auth: Optional[dict] = None  # cached auth state

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        if self._static_api_key:
            return self._static_api_key

        if self._auth is None:
            self._auth = _load_auth()

        if self._auth is None:
            raise RuntimeError(
                "No Codex credentials found. Run `codex login` to authenticate, "
                "or set CODEX_API_KEY."
            )

        # Refresh if expired (with 60s buffer) — expires is in epoch seconds
        if self._auth.get("expires", 0) < (time.time() + 60):
            logger.debug("Codex OAuth token expired, refreshing...")
            result = _do_refresh(self._auth.get("refresh", ""))
            if result:
                self._auth.update(
                    {
                        "access": result["access_token"],
                        "refresh": result["refresh_token"],
                        "expires": time.time() + result.get("expires_in", 3600),
                    }
                )
                _save_auth(self._auth)
            else:
                raise RuntimeError("Failed to refresh Codex OAuth token. Run `codex login`.")

        return self._auth["access"]

    async def _async_get_token(self) -> str:
        if self._static_api_key:
            return self._static_api_key

        if self._auth is None:
            self._auth = _load_auth()

        if self._auth is None:
            raise RuntimeError(
                "No Codex credentials found. Run `codex login` to authenticate, "
                "or set CODEX_API_KEY."
            )

        if self._auth.get("expires", 0) < (time.time() + 60):
            logger.debug("Codex OAuth token expired, refreshing async...")
            result = await _async_refresh(self._auth.get("refresh", ""))
            if result:
                self._auth.update(
                    {
                        "access": result["access_token"],
                        "refresh": result["refresh_token"],
                        "expires": time.time() + result.get("expires_in", 3600),
                    }
                )
                _save_auth(self._auth)
            else:
                raise RuntimeError("Failed to refresh Codex OAuth token. Run `codex login`.")

        return self._auth["access"]

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_request(self, messages: List[BaseMessage], tools: List[Tool]) -> tuple[dict, dict]:
        """Return (headers, body) for the Codex API call."""
        token = self._get_token()
        acct_id = _account_id(token)

        instructions, input_items = _convert_messages(messages)
        codex_tools = _convert_tools(tools) if tools else None

        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": acct_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        body: dict = {
            "model": self._model,
            "input": input_items,
            "store": False,
            "stream": True,
            "reasoning": {"effort": "medium", "summary": "auto"},
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        body["instructions"] = instructions or "You are a helpful assistant."
        if codex_tools:
            body["tools"] = codex_tools

        return headers, body

    async def _build_request_async(
        self, messages: List[BaseMessage], tools: List[Tool]
    ) -> tuple[dict, dict]:
        token = await self._async_get_token()
        acct_id = _account_id(token)

        instructions, input_items = _convert_messages(messages)
        codex_tools = _convert_tools(tools) if tools else None

        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": acct_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        body: dict = {
            "model": self._model,
            "input": input_items,
            "store": False,
            "stream": True,
            "reasoning": {"effort": "medium", "summary": "auto"},
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        body["instructions"] = instructions or "You are a helpful assistant."
        if codex_tools:
            body["tools"] = codex_tools

        return headers, body

    # ------------------------------------------------------------------
    # BaseChatLLM interface
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "codex"

    def invoke(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> LLMEvent:
        headers, body = self._build_request(messages, tools)
        url = self._base_url + CODEX_PATH
        events: list[dict] = []

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = _parse_sse_line(line)
                    if event:
                        events.append(event)

        return _extract_final(events)

    async def ainvoke(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> LLMEvent:
        headers, body = await self._build_request_async(messages, tools)
        url = self._base_url + CODEX_PATH
        events: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = _parse_sse_line(line)
                    if event:
                        events.append(event)

        return _extract_final(events)

    def stream(
        self,
        messages: List[BaseMessage],
        tools: List[Tool] = [],
        structured_output=None,
        json_mode: bool = False,
    ) -> Iterator[LLMStreamEvent]:
        headers, body = self._build_request(messages, tools)
        url = self._base_url + CODEX_PATH

        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args = ""
        text_started = False
        usage = None

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", url, headers=headers, json=body) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = _parse_sse_line(line)
                    if not event:
                        continue

                    etype = event.get("type", "")

                    if etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=delta)

                    elif etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            tool_name = item.get("name")
                            tool_call_id = item.get("call_id") or item.get("id")

                    elif etype == "response.function_call_arguments.delta":
                        tool_args += event.get("delta", "")

                    elif etype == "response.completed":
                        resp = event.get("response", {})
                        usage_data = resp.get("usage", {})
                        if usage_data:
                            usage = TokenUsage(
                                prompt_tokens=usage_data.get("input_tokens", 0),
                                completion_tokens=usage_data.get("output_tokens", 0),
                                total_tokens=usage_data.get("total_tokens", 0),
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
        headers, body = await self._build_request_async(messages, tools)
        url = self._base_url + CODEX_PATH

        tool_name: Optional[str] = None
        tool_call_id: Optional[str] = None
        tool_args = ""
        text_started = False
        usage = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    logger.error(
                        "Codex API error %s: %s", response.status_code, error_body.decode()
                    )
                response.raise_for_status()
                async for line in response.aiter_lines():
                    event = _parse_sse_line(line)
                    if not event:
                        continue

                    etype = event.get("type", "")

                    if etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=delta)

                    elif etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            tool_name = item.get("name")
                            tool_call_id = item.get("call_id") or item.get("id")

                    elif etype == "response.function_call_arguments.delta":
                        tool_args += event.get("delta", "")

                    elif etype == "response.completed":
                        resp = event.get("response", {})
                        usage_data = resp.get("usage", {})
                        if usage_data:
                            usage = TokenUsage(
                                prompt_tokens=usage_data.get("input_tokens", 0),
                                completion_tokens=usage_data.get("output_tokens", 0),
                                total_tokens=usage_data.get("total_tokens", 0),
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
        context_window = 128_000
        return Metadata(name=self._model, context_window=context_window, owned_by="openai")
