from __future__ import annotations
from program.message.types import ToolResultContent
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional
from program.agent.types import (
    EmitEvent, AgentEventType, TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    AgentStartEvent, AgentEndEvent,AgentErrorEvent
)
from program.llm.types import (
    LLMEventType, ErrorEvent, EndEvent, TextDeltaEvent, TextStartEvent, TextEndEvent,
    ThinkingDeltaEvent, ThinkingStartEvent, ThinkingEndEvent, ToolCallEndEvent, StopReason
)
from program.tool.types import ToolResult
from program.tool.registry import ToolRegistry
from program.message.types import AssistantMessage, ToolCallContent

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
from program.message.types import BaseMessage,ToolMessage


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
        self.tool_registry = ToolRegistry(tools)
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

    def process_events(self, event: AgentEvent) -> None:
        """Process an agent event."""
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

        tool_calls: list[ToolCallContent] = []
        tool_results: list[ToolResultContent] = []

        try:
            while True:
                emit(TurnStartEvent())
                message = AssistantMessage()
                tool_calls.clear()

                if self.options.transform_context is not None:
                    messages = self.options.transform_context(messages,signal)

                emit(MessageStartEvent(message=message))
                async for event in self.llm.stream(messages, tools=self.tools):
                    match event:
                        case ToolCallEndEvent(tool_call=tool_call):
                            tool_calls.append(tool_call)
                            message.contents.append(tool_call)
                        case TextDeltaEvent(text=text):
                            emit(MessageUpdateEvent(message=AssistantMessage(contents=[text])))
                        case ThinkingDeltaEvent(thinking=thinking):
                            emit(MessageUpdateEvent(message=AssistantMessage(contents=[thinking])))
                        case TextEndEvent(text=text):
                            message.contents.append(text)
                        case ThinkingEndEvent(thinking=thinking):
                            message.contents.append(thinking)
                        case ErrorEvent(reason=reason, error=error):
                            message.stop_reason = reason
                            message.error = error
                        case EndEvent(reason=reason):
                            message.stop_reason = reason
                
                emit(MessageEndEvent(message=message))
                messages.append(message)

                match message.stop_reason:
                    case StopReason.Error | StopReason.Abort:
                        err_msg = message.error or f"Turn failed with reason: {message.stop_reason.value}"
                        emit(AgentErrorEvent(error=err_msg))
                        emit(TurnEndEvent(message=message, tool_results=tool_results))
                        break

                    case StopReason.ToolCalls:
                        tool_results = await self._execute_tool_calls(
                            tool_calls=tool_calls, 
                            emit=emit, 
                            signal=signal
                        )
                        tool_messages = [
                            ToolMessage(contents=[
                                tool_result
                            ]) for tool_result in tool_results
                        ]
                        for msg in tool_messages:
                            emit(MessageStartEvent(message=msg))
                            emit(MessageEndEvent(message=msg))
                        messages.extend(tool_messages)

                        if steering_messages:=self.options.get_steering_messages():
                            for msg in steering_messages:
                                emit(MessageStartEvent(message=msg))
                                emit(MessageEndEvent(message=msg))
                            messages.extend(steering_messages)

                    case StopReason.Stop:
                        if follow_up_messages:=self.options.get_follow_up_messages():
                            for msg in follow_up_messages:
                                emit(MessageStartEvent(message=msg))
                                emit(MessageEndEvent(message=msg))
                            messages.extend(follow_up_messages)
                        else:
                            # No more work to do
                            emit(TurnEndEvent(message=message, tool_results=tool_results))
                            break
                
                emit(TurnEndEvent(message=message, tool_results=tool_results))

                # Check if an external hook wants to stop the turn
                if self.options.should_stop_after_turn:
                    if self.options.should_stop_after_turn(message, tool_results):
                        break
                
                tool_results.clear()
        except Exception as e:
            emit(AgentErrorEvent(error=str(e)))

        emit(AgentEndEvent(messages=messages))

    async def _execute_tool_calls(self, tool_calls: list[ToolCallContent], emit: EmitEvent, signal:Optional[AbortSignal]=None)->list[ToolResultContent]:
        tool_results=await self.tool_registry.batch_execute(
            tool_calls=tool_calls,
            options=self.options,
            emit=emit,
            signal=signal,
            _llm=self.llm
        )
        return tool_results

    async def run(self, messages: list[BaseMessage]):
        signal: AbortSignal = asyncio.Event()
        self.state.is_streaming = True
        self.state.error_message=""

        await self._loop(messages, self.process_events, signal)
        self.state.messages = messages
        self.state.is_streaming = False


