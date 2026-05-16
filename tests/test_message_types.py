"""Tests for message construction and content access."""
import pytest
from program.message.types import (
    TextContent, ThinkingContent, ToolCallContent, ToolResultContent,
    UserMessage, AssistantMessage, SystemMessage, ToolMessage,
    Role, Usage,
)
from program.inference.types import StopReason


class TestTextContent:
    def test_default_type(self):
        c = TextContent()
        assert c.type == "text"
        assert c.content == ""

    def test_with_content(self):
        c = TextContent(content="hello")
        assert c.content == "hello"


class TestUserMessage:
    def test_role(self):
        msg = UserMessage.text("hi")
        assert msg.role == Role.USER

    def test_text_factory(self):
        msg = UserMessage.text("hello world")
        assert len(msg.contents) == 1
        assert isinstance(msg.contents[0], TextContent)
        assert msg.contents[0].content == "hello world"

    def test_has_id_and_timestamp(self):
        msg = UserMessage.text("hi")
        assert msg.id
        assert msg.timestamp > 0


class TestSystemMessage:
    def test_role(self):
        msg = SystemMessage.text("be helpful")
        assert msg.role == Role.SYSTEM

    def test_content(self):
        msg = SystemMessage.text("system prompt")
        assert msg.contents[0].content == "system prompt"


class TestAssistantMessage:
    def test_role(self):
        msg = AssistantMessage()
        assert msg.role == Role.ASSISTANT

    def test_defaults(self):
        msg = AssistantMessage()
        assert msg.stop_reason == StopReason.Stop
        assert msg.error == ""
        assert isinstance(msg.usage, Usage)

    def test_text_content(self):
        msg = AssistantMessage(contents=[
            TextContent(content="Hello"),
            TextContent(content=" world"),
        ])
        assert msg.text_content() == "Hello world"

    def test_text_content_ignores_non_text(self):
        msg = AssistantMessage(contents=[
            TextContent(content="hi"),
            ThinkingContent(content="thinking..."),
            ToolCallContent(id="1", name="tool"),
        ])
        assert msg.text_content() == "hi"

    def test_tool_calls(self):
        tc = ToolCallContent(id="abc", name="my_tool", args={"x": 1})
        msg = AssistantMessage(contents=[TextContent(content="ok"), tc])
        assert msg.tool_calls() == [tc]

    def test_thinking(self):
        th = ThinkingContent(content="step 1")
        msg = AssistantMessage(contents=[th, TextContent(content="answer")])
        assert msg.thinking() == [th]


class TestToolMessage:
    def test_role(self):
        result = ToolResultContent(id="1", content="done")
        msg = ToolMessage.from_result(result)
        assert msg.role == Role.TOOL

    def test_from_results(self):
        results = [
            ToolResultContent(id="1", content="a"),
            ToolResultContent(id="2", content="b"),
        ]
        msg = ToolMessage.from_results(results)
        assert len(msg.contents) == 2
