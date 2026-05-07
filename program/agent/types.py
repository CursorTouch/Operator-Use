from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from program.llm.service import LLM
    from program.llm.types import ThinkingLevel, LLMEvent
    from program.tool.types import Tool

from program.message.types import BaseMessage, ToolCallContent


@dataclass
class AgentState:
    system_prompt: Optional[str] = None
    messages: list[BaseMessage] = field(default_factory=list)
    pending_tool_calls: list[ToolCallContent] = field(default_factory=list)
    is_streaming: bool = False
    llm: Optional[LLM] = None
    thinking_level: Optional[ThinkingLevel] = None
    error_message: Optional[str] = None
    tools: list[Tool] = field(default_factory=list)


# Agent lifecycle
@dataclass
class AgentStartEvent:
    type: str = field(default="agent_start", init=False)


@dataclass
class AgentEndEvent:
    type: str = field(default="agent_end", init=False)
    messages: list[BaseMessage] = field(default_factory=list)


# Turn lifecycle
@dataclass
class TurnStartEvent:
    type: str = field(default="turn_start", init=False)


@dataclass
class TurnEndEvent:
    type: str = field(default="turn_end", init=False)
    message: Optional[BaseMessage] = None
    tool_results: list[BaseMessage] = field(default_factory=list)


# Message lifecycle
@dataclass
class MessageStartEvent:
    type: str = field(default="message_start", init=False)
    message: Optional[BaseMessage] = None


@dataclass
class MessageUpdateEvent:
    type: str = field(default="message_update", init=False)
    message: Optional[BaseMessage] = None


@dataclass
class MessageEndEvent:
    type: str = field(default="message_end", init=False)
    message: Optional[BaseMessage] = None


# Tool execution lifecycle
@dataclass
class ToolExecutionStartEvent:
    type: str = field(default="tool_execution_start", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionUpdateEvent:
    type: str = field(default="tool_execution_update", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionEndEvent:
    type: str = field(default="tool_execution_end", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    content: Any = None
    is_error: bool = False


AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)
