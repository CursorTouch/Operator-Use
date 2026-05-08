from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional
from program.agent.types import (
    EmitEvent, AgentEventType, TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    AgentStartEvent, AgentEndEvent
)
from program.llm.types import (
    LLMEventType, ErrorEvent, EndEvent, TextDeltaEvent, TextStartEvent, TextEndEvent,
    ThinkingDeltaEvent, ThinkingStartEvent, ThinkingEndEvent, ToolCallEndEvent, StopReason
)
from program.message.types import AssistantMessage, ToolMessage, UserMessage, TextContent, ThinkingContent, ToolCallContent
from program.tool.types import ToolResult

if TYPE_CHECKING:
    from program.llm.service import LLM
    from program.tool.types import Tool

from program.agent.types import (
    AgentState,
    Options,
    AgentEvent,
    FollowupQueue,
    SteeringQueue,
    AbortSignal,
)
from program.message.types import BaseMessage


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        system_prompt: Optional[str] = None,
        options: Optional[Options] = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.options = options or Options()
        self.state = AgentState(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            follow_up_queue=FollowupQueue(mode=self.options.followup_mode),
            steering_queue=SteeringQueue(mode=self.options.steering_mode),
        )

    async def steer(self, message: BaseMessage) -> None:
        """Add a steering message to the steering queue."""
        await self.state.steering_queue.add(message)

    async def follow_up(self, message: BaseMessage) -> None:
        """Add a follow-up message to the follow-up queue."""
        await self.state.follow_up_queue.add(message)

    def clear_steering(self) -> None:
        """Clear all messages from the steering queue."""
        self.state.steering_queue.clear()

    def clear_follow_up(self) -> None:
        """Clear all messages from the follow-up queue."""
        self.state.follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        """Clear all messages from the steering and follow-up queues."""
        self.state.steering_queue.clear()
        self.state.follow_up_queue.clear()

    def has_pending_messages(self) -> bool:
        """Check if there are any pending messages."""
        return not self.state.steering_queue.is_empty() or not self.state.follow_up_queue.is_empty()

    def reset(self) -> None:
        """Reset agent state: clear queues, error message, tool calls, and streaming flag."""
        self.state.follow_up_queue.clear()
        self.state.steering_queue.clear()
        self.state.error_message = None
        self.state.pending_tool_calls.clear()
        self.state.is_streaming = False

    def process_events(self,event:AgentEvent):
        match event.type:
            case AgentEventType.AgentStart:
                pass
            case AgentEventType.AgentEnd:
                pass
            case AgentEventType.TurnStart:
                pass
            case AgentEventType.TurnEnd:
                pass
            case AgentEventType.MessageStart:
                pass
            case AgentEventType.MessageUpdate:
                pass
            case AgentEventType.MessageEnd:
                pass
            case AgentEventType.ToolExecutionStart:
                pass
            case AgentEventType.ToolExecutionUpdate:
                pass
            case AgentEventType.ToolExecutionEnd:
                pass
    
    async def _loop(self, messages: list[BaseMessage], emit: EmitEvent, signal: AbortSignal):
        emit(AgentStartEvent())
        for message in messages:
            emit(MessageStartEvent(message=message))
            emit(MessageEndEvent(message=message))

        while True:
            emit(TurnStartEvent())
            message=AssistantMessage()
            tool_calls: list[ToolCallContent]=[]
            
            async for event in self.llm.stream(messages):
                match event:
                    case ToolCallEndEvent(tool_call=tool_call):
                        message.contents.append(tool_call)
                        tool_calls.append(tool_call)
                    case TextEndEvent(text=text):
                        message.contents.append(text)
                    case ThinkingEndEvent(thinking=thinking):
                        message.contents.append(thinking)
                    case EndEvent(reason=reason):
                        message.stop_reason=reason
                        emit(TurnEndEvent(message=message,tool_results=[]))
                        emit(AgentEndEvent(messages=messages))
                        break
                
            tool_results=await self._execute_tool_calls(tool_calls=tool_calls)
                    
            emit(TurnEndEvent(message=message,tool_results=[]))
        
        emit(AgentEndEvent(messages=messages))

    async def _execute_tool_calls(self, tool_calls: list[ToolCallContent])->list[ToolResult]:
        pass

    async def run(self, messages: list[BaseMessage]):
        signal: AbortSignal = asyncio.Event()
        self.state.is_streaming = True
        self.state.error_message=""

        try:
            await self._loop(messages,self.process_events,signal)
        except Exception as e:
            pass


