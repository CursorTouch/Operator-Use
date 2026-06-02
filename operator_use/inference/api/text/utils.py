"""Shared utilities for LLM API provider implementations."""
from __future__ import annotations

import json
from typing import Any


__all__ = ["parse_tool_args", "openai_user_content", "openai_assistant_content", "openai_messages_to_chat"]


def parse_tool_args(value: Any) -> dict:
    """Parse a tool-call arguments value into a dict.

    Handles the three shapes that provider APIs return:
    - already a dict  → return as-is
    - a JSON string   → parse and return (empty string → {})
    - anything else   → return {}
    Falls back to {} on JSONDecodeError.
    """
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def openai_user_content(content_items: list) -> str | list[dict[str, Any]]:
    """Convert user message contents to OpenAI chat format (completions/copilot/mistral)."""
    from operator_use.message.types import TextContent, ImageContent
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


def openai_assistant_content(content_items: list) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert assistant message contents to OpenAI chat format (completions/copilot)."""
    from operator_use.message.types import TextContent, ToolCallContent
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


def openai_messages_to_chat(messages: list) -> list[dict[str, Any]]:
    """Convert a message list to OpenAI chat completions format."""
    from operator_use.message.types import (
        SystemMessage, UserMessage, AssistantMessage, ToolMessage,
        TextContent, ToolResultContent,
    )
    result: list[dict[str, Any]] = []
    for msg in messages:
        match msg:
            case SystemMessage():
                text = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
                result.append({"role": "system", "content": text})
            case UserMessage():
                result.append({"role": "user", "content": openai_user_content(msg.contents)})
            case AssistantMessage():
                text, tool_calls = openai_assistant_content(msg.contents)
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
