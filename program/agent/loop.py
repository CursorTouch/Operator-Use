from __future__ import annotations
from program.message.types import ToolResultContent
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional, Callable
from program.agent.types import (
    EmitEvent, AgentEventType, TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    AgentStartEvent, AgentEndEvent,AgentErrorEvent,ToolExecutionMode
)
from program.llm.types import (
    LLMEventType, ErrorEvent, EndEvent, TextDeltaEvent, TextStartEvent, TextEndEvent,
    ThinkingDeltaEvent, ThinkingStartEvent, ThinkingEndEvent, ToolCallEndEvent, StopReason
)
from program.tool.types import ToolResult
from program.tool.registry import ToolRegistry
from program.message.types import AssistantMessage, ToolCallContent, Role

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
        self.listeners: list[Callable[[AgentEvent], None]] = []
        self.state = AgentState(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            follow_up_queue=FollowupQueue(mode=self.options.followup_mode),
            steering_queue=SteeringQueue(mode=self.options.steering_mode),
        )

    async def steer(self, message: BaseMessage) -> None:
        """Add a steering message to the steering queue."""
        await self.state.steering_queue.enqueue(message)

    async def follow_up(self, message: BaseMessage) -> None:
        """Add a follow-up message to the follow-up queue."""
        await self.state.follow_up_queue.enqueue(message)

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

    async def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable[[], None]:
        """Subscribe to agent events."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)
    
    async def process_events(self, event: AgentEvent) -> None:  
        """Process an agent event and update internal state."""  
        match event:
            case MessageStartEvent(message=message):  
                self.state.streaming_message = message  
            case MessageUpdateEvent(message=message):  
                self.state.streaming_message = message  
            case MessageEndEvent(message=message):  
                self.state.streaming_message = None  
                self.state.messages.append(message)  
            case ToolExecutionStartEvent(tool_call=tool_call):  
                self.state.pending_tool_calls.add(tool_call.id)  
            case ToolExecutionEndEvent(tool_result=tool_result):  
                self.state.pending_tool_calls.discard(tool_result.id)
            case AgentErrorEvent(error=error):  
                self.state.error_message = error
        # Notify all listeners  
        await asyncio.gather(*[listener(event) for listener in self.listeners])

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
                            messages.append(msg)

                        if steering_messages:=self.options.get_steering_messages():
                            for msg in steering_messages:
                                emit(MessageStartEvent(message=msg))
                                emit(MessageEndEvent(message=msg))
                                messages.append(msg)

                    case StopReason.Stop:
                        if follow_up_messages:=self.options.get_follow_up_messages():
                            for msg in follow_up_messages:
                                emit(MessageStartEvent(message=msg))
                                emit(MessageEndEvent(message=msg))
                                messages.append(msg)
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
        match self.options.execution_mode:
            case ToolExecutionMode.Batch:
                return await self.tool_registry.batch_execute(
                    tool_calls=tool_calls,
                    options=self.options,
                    emit=emit,
                    signal=signal,
                    _llm=self.llm
                )
            case ToolExecutionMode.Parallel:
                return await self.tool_registry.parallel_execute(
                    tool_calls=tool_calls,
                    options=self.options,
                    emit=emit,
                    signal=signal,
                    _llm=self.llm
                )
            case ToolExecutionMode.Sequential:
                return await self.tool_registry.sequential_execute(
                    tool_calls=tool_calls,
                    options=self.options,
                    emit=emit,
                    signal=signal,
                    _llm=self.llm
                )

    async def run(self, messages: list[BaseMessage]):
        signal: AbortSignal = asyncio.Event()
        self.state.is_streaming = True
        await self._loop(messages, self.process_events, signal)
        self.state.is_streaming = False

    async def run_continue(self) -> None:  
        """Continue from the current transcript. The last message must be a user or tool-result message."""  
        if self.state.is_streaming:  
            raise RuntimeError("Agent is already processing. Wait for completion before continuing.")  
        
        if not self.state.messages:  
            raise RuntimeError("No messages to continue from")  
        
        last_message = self.state.messages[-1]  
        if last_message.role == Role.ASSISTANT:  
            # Check for queued steering messages first  
            if not self.state.steering_queue.is_empty():  
                steering_messages = await self.state.steering_queue.dequeue()  
                await self.run(steering_messages)  
                return  
            
            # Check for queued follow-up messages  
            if not self.state.follow_up_queue.is_empty():  
                follow_up_messages = await self.state.follow_up_queue.dequeue()  
                await self.run(follow_up_messages)  
                return  
            
            raise RuntimeError("Cannot continue from message role: assistant")  
        
        await self._loop_continue()  
    
    async def _loop_continue(self) -> None:  
        """Continue the agent loop from existing context without adding new messages."""  
        signal: AbortSignal = asyncio.Event()  
        self.state.is_streaming = True   
        await self._loop(self.state.messages, self.process_events, signal)  
        self.state.is_streaming = False


