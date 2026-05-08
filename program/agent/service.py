from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional
from program.agent.types import (
    EmitEvent,AgentEventType,TurnStartEvent,TurnEndEvent,
    MessageStartEvent,MessageEventData,MessageEndEvent,ToolCallStartEvent,
    ToolCallEventData,ToolCallEndEvent,AgentStartEvent,AgentEndEvent
)
from program.llm.types import LLMEventType,ErrorEvent,EndEvent,TextDeltaEvent,TextStartEvent,TextEndEvent,ThinkingDeltaEvent,ThinkingStartEvent,ThinkingEndEvent


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
    
    async def _loop(self,messages:list[BaseMessage],emit:EmitEvent,signal:AbortSignal):
        emit(AgentStartEvent())
        pending_messages= (self.options.get_follow_up_messages() or [])+messages
        for message in pending_messages:
            emit(MessageStartEvent(message=message))
            emit(MessageEndEvent(message=message))
            self.state.messages.append(message)

            has_tool_calls=False
            while has_tool_calls or len(pending_messages):
                emit(TurnStartEvent())
                llm_event=self.llm.stream(self.state.messages)

                for event in llm_event:
                    match event.type:
                        case LLMEventType.Start:
                            pass
                        case LLMEventType.Error:
                            pass
                        case LLMEventType.Done:
                            pass
                        case LLMEventType.TextStart:
                            pass
                        case LLMEventType.TextDelta:
                            pass
                        case LLMEventType.TextEnd:
                            pass
                        case LLMEventType.ThinkingStart:
                            pass
                        case LLMEventType.ThinkingDelta:
                            pass
                        case LLMEventType.ThinkingEnd:
                            pass
                        case LLMEventType.ToolCallStart:
                            pass
                        case LLMEventType.ToolCallDelta:
                            pass
                        case LLMEventType.ToolCallEnd:
                            pass
                

                
                
                

                

                emit(TurnEndEvent(message=message))
                
                
                

                

                emit(TurnEndEvent(message=message))
        


    async def _loop_continue(self,messages:list[BaseMessage],emit:EmitEvent,signal:AbortSignal):
        pass

    async def run(self, messages: list[BaseMessage]):
        signal: AbortSignal = asyncio.Event()
        self.state.is_streaming = True
        self.state.error_message=""

        try:
            await self._loop(messages,self.process_events,signal)
        except Exception as e:
            pass


