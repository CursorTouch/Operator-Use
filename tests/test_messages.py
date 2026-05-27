"""Message types — content extraction, factory methods, image content, thinking content."""
from __future__ import annotations

import pytest

from operator_use.message.types import (
    TextContent, ToolCallContent, ToolResultContent, ThinkingContent, ImageContent,
    UserMessage, AssistantMessage, ToolMessage, Role, Usage,
)


class TestAssistantMessageContent:
    def test_text_content_extraction(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="hello"), TextContent(content=" world")]
        assert msg.text_content() == "hello world"

    def test_text_content_empty(self):
        msg = AssistantMessage()
        assert msg.text_content() == ""

    def test_tool_calls_extraction(self):
        tc = ToolCallContent(id="t1", name="fn", args={"x": 1})
        msg = AssistantMessage()
        msg.contents = [TextContent(content="prefix"), tc]
        calls = msg.tool_calls()
        assert len(calls) == 1
        assert calls[0].name == "fn"

    def test_no_tool_calls_returns_empty(self):
        msg = AssistantMessage()
        msg.contents = [TextContent(content="text only")]
        assert msg.tool_calls() == []

    def test_thinking_extraction(self):
        th = ThinkingContent(content="pondering")
        msg = AssistantMessage()
        msg.contents = [th, TextContent(content="answer")]
        thinking = msg.thinking()
        assert len(thinking) == 1
        assert thinking[0].content == "pondering"

    def test_usage_accumulates(self):
        msg = AssistantMessage()
        msg.usage = Usage(input_tokens=100, output_tokens=50)
        assert msg.usage.input_tokens == 100
        assert msg.usage.output_tokens == 50

    def test_role_is_assistant(self):
        assert AssistantMessage().role == Role.ASSISTANT


class TestToolMessage:
    def test_from_results_copies_list(self):
        original = [ToolResultContent(id="t1", content="r1")]
        msg = ToolMessage.from_results(original)
        original.append(ToolResultContent(id="t2", content="r2"))
        assert len(msg.contents) == 1  # snapshot, not reference

    def test_role_is_tool(self):
        msg = ToolMessage.from_results([ToolResultContent(id="t1", content="x")])
        assert msg.role == Role.TOOL

    def test_error_result_preserved(self):
        results = [ToolResultContent(id="t1", content="err", is_error=True)]
        msg = ToolMessage.from_results(results)
        assert msg.contents[0].is_error is True


class TestUserMessage:
    def test_text_factory(self):
        msg = UserMessage.text("hello")
        assert msg.role == Role.USER
        assert msg.contents[0].content == "hello"

    def test_text_factory_returns_user_message(self):
        assert isinstance(UserMessage.text("x"), UserMessage)


class TestImageContent:
    def test_from_url_passthrough(self):
        img = ImageContent.from_url("https://example.com/img.png")
        pairs = img.to_base64()
        assert pairs[0][0] == "https://example.com/img.png"
        assert pairs[0][1] == ""

    def test_jpeg_mime_detected(self):
        jpeg = b"\xff\xd8\xff" + b"\x00" * 100
        img = ImageContent(images=[jpeg])
        pairs = img.to_base64()
        assert "jpeg" in pairs[0][1]

    def test_png_mime_detected(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        img = ImageContent(images=[png])
        pairs = img.to_base64()
        assert "png" in pairs[0][1]


class TestThinkingContent:
    def test_thinking_content_stores_text(self):
        th = ThinkingContent(content="deep thought")
        assert th.content == "deep thought"

    def test_thinking_in_assistant_message(self):
        msg = AssistantMessage()
        msg.contents = [ThinkingContent(content="t"), TextContent(content="answer")]
        assert len(msg.thinking()) == 1
        assert msg.text_content() == "answer"
