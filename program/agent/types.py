from __future__ import annotations
from asyncio import Queue
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional
import asyncio

if TYPE_CHECKING:
    from program.llm.service import LLM
    from program.llm.types import ThinkingLevel
    from program.tool.types import Tool, ToolExecutionMode

from program.message.types import BaseMessage, ToolCallContent
from program.tool.types import ToolInvocation, ToolResult

AbortSignal = asyncio.Event


class SteeringMode(str, Enum):
    OneAtATime = "one_at_a_time"
    All = "all"


class FollowupMode(str, Enum):
    OneAtATime = "one_at_a_time"
    All = "all"


AfterToolCallCallback = Callable[[ToolResult, Optional[AbortSignal]], Optional[ToolResult]]
BeforeToolCallCallback = Callable[[ToolInvocation, Optional[AbortSignal]], Optional[ToolInvocation]]
GetFollowUpMessagesCallback = Callable[[], list[BaseMessage]]
GetSteeringMessagesCallback = Callable[[], list[BaseMessage]]
ShouldStopAfterTurnCallback = Callable[[list[BaseMessage]], bool]
TransformContextCallback = Callable[[list[BaseMessage], Optional[AbortSignal]], list[BaseMessage]]


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
    follow_up_queue: Optional[FollowupQueue] = None
    steering_queue: Optional[SteeringQueue] = None


@dataclass
class Options:
    after_tool_call: Optional[AfterToolCallCallback] = None
    before_tool_call: Optional[BeforeToolCallCallback] = None
    execution_mode: Optional[ToolExecutionMode] = None
    steering_mode: SteeringMode = SteeringMode.OneAtATime
    followup_mode: FollowupMode = FollowupMode.OneAtATime
    get_follow_up_messages: Optional[GetFollowUpMessagesCallback] = None
    get_steering_messages: Optional[GetSteeringMessagesCallback] = None
    should_stop_after_turn: Optional[ShouldStopAfterTurnCallback] = None
    transform_context: Optional[TransformContextCallback] = None


@dataclass
class FollowupQueue:
    mode: FollowupMode
    queue: Queue[BaseMessage] = field(default_factory=Queue)

    def clear(self):
        self.queue = Queue()

    async def add(self, message: BaseMessage):
        await self.queue.put(message)
    
    def is_empty(self) -> bool:
        return self.queue.empty()

    async def drain(self) -> list[BaseMessage]:
        messages = []
        if self.mode == FollowupMode.OneAtATime:
            if not self.is_empty():
                messages.append(await self.queue.get())
        else:
            while not self.is_empty():
                messages.append(await self.queue.get())
        return messages


@dataclass
class SteeringQueue:
    mode: SteeringMode
    queue: Queue[BaseMessage] = field(default_factory=Queue)


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
