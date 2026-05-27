from __future__ import annotations
from asyncio import Queue
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
import asyncio

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.inference.types import ThinkingLevel
    from operator_use.tool.types import Tool

from operator_use.message.types import BaseMessage, ToolCallContent, ToolResultContent
from operator_use.tool.types import ToolInvocation, ToolResult, ToolExecutionMode

AbortSignal = asyncio.Event
EmitEvent = Callable[['AgentEvent'], Awaitable[None]]

class SteeringMode(str, Enum):
    OneAtATime = "one_at_a_time"
    All = "all"


class FollowupMode(str, Enum):
    OneAtATime = "one_at_a_time"
    All = "all"


class AgentEventType(str, Enum):
    AgentStart = "agent_start"
    AgentEnd = "agent_end"
    TurnStart = "turn_start"
    TurnEnd = "turn_end"
    MessageStart = "message_start"
    MessageUpdate = "message_update"
    MessageEnd = "message_end"
    ToolExecutionStart = "tool_execution_start"
    ToolExecutionUpdate = "tool_execution_update"
    ToolExecutionEnd = "tool_execution_end"
    AgentError = "agent_error"


# Hook event types — canonical definitions live in program.hooks
from operator_use.hooks.types import (
    AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    ToolExecutionFailureEvent,
    BeforeProviderRequestEvent, AfterProviderResponseEvent, QueueUpdateEvent,
)

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
    | ToolExecutionFailureEvent
    | AgentErrorEvent
)

AfterToolCallCallback = Callable[[ToolInvocation, ToolResult, Optional[AbortSignal]], Awaitable[Optional[ToolResult]]]
BeforeToolCallCallback = Callable[[ToolInvocation, Optional[AbortSignal]], Awaitable[Optional[ToolInvocation | ToolResultContent]]]
GetFollowUpMessagesCallback = Callable[[], list[BaseMessage]]
GetSteeringMessagesCallback = Callable[[], list[BaseMessage]]
OnEventCallback = Callable[['AgentEvent'], Awaitable[None]]
ShouldSkipToolCallsCallback = Callable[[ToolCallContent], ToolResultContent]
ShouldStopAfterTurnCallback = Callable[[BaseMessage,list[ToolResultContent]], bool]
TransformContextCallback = Callable[[list[BaseMessage], Optional[AbortSignal]], list[BaseMessage]]


@dataclass
class AgentState:
    system_prompt: Optional[str] = None
    messages: list[BaseMessage] = field(default_factory=list)
    pending_tool_calls: set[str] = field(default_factory=set)
    is_streaming: bool = False
    llm: Optional[LLM] = None
    streaming_message: Optional[BaseMessage] = None
    thinking_level: Optional[ThinkingLevel] = None
    error_message: Optional[str] = None
    tools: list[Tool] = field(default_factory=list)
    follow_up_queue: Optional[FollowupQueue] = None
    steering_queue: Optional[SteeringQueue] = None


@dataclass
class Options:
    after_tool_call: Optional[AfterToolCallCallback] = None
    before_tool_call: Optional[BeforeToolCallCallback] = None
    on_event: Optional[OnEventCallback] = None
    execution_mode: Optional[ToolExecutionMode] = None
    steering_mode: SteeringMode = SteeringMode.OneAtATime
    followup_mode: FollowupMode = FollowupMode.OneAtATime
    get_follow_up_messages: Optional[GetFollowUpMessagesCallback] = None
    get_steering_messages: Optional[GetSteeringMessagesCallback] = None
    should_stop_after_turn: Optional[ShouldStopAfterTurnCallback] = None
    should_skip_tool_calls: Optional[ShouldSkipToolCallsCallback] = None
    transform_context: Optional[TransformContextCallback] = None


@dataclass
class FollowupQueue:
    mode: FollowupMode
    queue: Queue[BaseMessage] = field(default_factory=Queue)

    def clear(self):
        self.queue = Queue()

    async def enqueue(self, message: BaseMessage):
        await self.queue.put(message)

    def is_empty(self) -> bool:
        return self.queue.empty()

    def snapshot(self) -> list[BaseMessage]:
        return list(self.queue._queue)  # type: ignore[attr-defined]

    async def dequeue(self) -> list[BaseMessage]:
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

    def clear(self):
        self.queue = Queue()

    async def enqueue(self, message: BaseMessage):
        await self.queue.put(message)

    def is_empty(self) -> bool:
        return self.queue.empty()

    def snapshot(self) -> list[BaseMessage]:
        return list(self.queue._queue)  # type: ignore[attr-defined]

    async def dequeue(self) -> list[BaseMessage]:
        messages = []
        if self.mode == SteeringMode.OneAtATime:
            if not self.is_empty():
                messages.append(await self.queue.get())
        else:
            while not self.is_empty():
                messages.append(await self.queue.get())
        return messages


