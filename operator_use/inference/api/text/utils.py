"""Shared utilities for LLM API provider implementations."""
from __future__ import annotations

import json
from typing import Any


__all__ = [
    "parse_tool_args",
    "openai_user_content", "openai_assistant_content", "openai_messages_to_chat", "openai_response_format",
    "anthropic_messages_to_list", "anthropic_output_config",
]


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


def openai_response_format(response_format: Any | None) -> dict[str, Any] | None:
    """Convert response_format to OpenAI json_schema format (completions/copilot/mistral)."""
    from operator_use.inference.types import normalize_structured_response_format
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


def anthropic_messages_to_list(messages: list) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert a message list to Anthropic Messages API format."""
    from operator_use.message.types import (
        SystemMessage, UserMessage, AssistantMessage, ToolMessage,
        TextContent, ImageContent, ThinkingContent, ToolCallContent, ToolResultContent,
    )
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
                    result.append({"role": "user", "content": tool_results})
    return system, result


def anthropic_output_config(response_format: Any | None) -> dict[str, Any] | None:
    """Convert response_format to Anthropic output config format."""
    from operator_use.inference.types import normalize_structured_response_format
    structured = normalize_structured_response_format(response_format)
    if structured is None:
        return None
    return {"format": {"type": "json_schema", "schema": structured.schema}}
