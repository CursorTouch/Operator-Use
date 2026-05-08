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
from program.llm.types import LLMEventType,ErrorEvent,EndEvent,TextDeltaEvent,TextStartEvent,TextEndEvent,ThinkingDeltaEvent,ThinkingStartEvent,ThinkingEndEvent,StopReason
from program.message.types import AssistantMessage,ToolMessage,UserMessage

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
        self.state.messages = list(messages)
        
        # Initial follow-up messages
        if self.options.get_follow_up_messages:
            initial_followups = self.options.get_follow_up_messages()
            for msg in initial_followups:
                emit(MessageStartEvent(message=msg))
                emit(MessageEndEvent(message=msg))
                self.state.messages.append(msg)

        while True:
            if signal.is_set():
                break

            # Check steering queue
            if self.state.steering_queue:
                steering_msgs = await self.state.steering_queue.drain()
                for msg in steering_msgs:
                    emit(MessageStartEvent(message=msg))
                    emit(MessageEndEvent(message=msg))
                    self.state.messages.append(msg)

            emit(TurnStartEvent())
            
            current_assistant_message = AssistantMessage()
            self.state.messages.append(current_assistant_message)
            
            has_error = False
            async for event in self.llm.stream(self.state.messages[:-1]): # Don't send the empty assistant message we just added
                if signal.is_set():
                    has_error = True
                    break
                
                match event.type:
                    case LLMEventType.Start:
                        pass
                    case LLMEventType.TextStart:
                        emit(MessageStartEvent(message=current_assistant_message))
                    case LLMEventType.TextDelta:
                        current_assistant_message.contents.append(event.text)
                        emit(MessageUpdateEvent(message=current_assistant_message))
                    case LLMEventType.TextEnd:
                        pass
                    case LLMEventType.ThinkingStart:
                        pass
                    case LLMEventType.ThinkingDelta:
                        if event.thinking:
                            current_assistant_message.contents.append(event.thinking)
                            emit(MessageUpdateEvent(message=current_assistant_message))
                    case LLMEventType.ThinkingEnd:
                        pass
                    case LLMEventType.ToolCallStart:
                        if event.tool_call:
                            current_assistant_message.contents.append(event.tool_call)
                            emit(MessageUpdateEvent(message=current_assistant_message))
                    case LLMEventType.ToolCallDelta:
                        # Update tool call args if needed, but usually we wait for ToolCallEnd
                        pass
                    case LLMEventType.ToolCallEnd:
                        if event.tool_call:
                            # Replace the partial tool call with the complete one
                            for i, content in enumerate(current_assistant_message.contents):
                                if isinstance(content, ToolCallContent) and content.id == event.tool_call.id:
                                    current_assistant_message.contents[i] = event.tool_call
                                    break
                            emit(MessageUpdateEvent(message=current_assistant_message))
                    case LLMEventType.Done:
                        break
                    case LLMEventType.Error:
                        self.state.error_message = event.error
                        has_error = True
                        break

            if has_error:
                emit(TurnEndEvent(message=current_assistant_message))
                break

            emit(MessageEndEvent(message=current_assistant_message))

            # Execute tool calls
            tool_calls = [c for c in current_assistant_message.contents if isinstance(c, ToolCallContent)]
            tool_results: list[BaseMessage] = []
            
            if tool_calls:
                # For now, execute tools sequentially. 
                # TODO: Implement parallel execution if requested.
                for tc in tool_calls:
                    if signal.is_set():
                        break
                    
                    tool = next((t for t in self.tools if t.name == tc.name), None)
                    if not tool:
                        res = ToolMessage(id=tc.id, contents=[TextContent(content=f"Tool '{tc.name}' not found.")])
                        tool_results.append(res)
                        continue

                    emit(ToolExecutionStartEvent(tool_call_id=tc.id, tool_name=tc.name, args=tc.args))
                    
                    try:
                        # Validate params
                        is_valid, errs = tool.validate(tc.args)
                        if not is_valid:
                            result = ToolResult.error(content=f"Invalid parameters: {', '.join(errs)}")
                        else:
                            invocation = ToolInvocation(params=tc.args)
                            result = await tool.execute(invocation, signal=signal)
                        
                        emit(ToolExecutionEndEvent(tool_call_id=tc.id, tool_name=tc.name, content=result.content, is_error=result.is_error))
                        
                        # Wrap result in ToolMessage
                        res_content = str(result.content) if not isinstance(result.content, (str, bytes)) else result.content
                        res = ToolMessage(id=tc.id, contents=[TextContent(content=res_content)])
                        tool_results.append(res)
                        self.state.messages.append(res)
                    except Exception as e:
                        emit(ToolExecutionEndEvent(tool_call_id=tc.id, tool_name=tc.name, content=str(e), is_error=True))
                        res = ToolMessage(id=tc.id, contents=[TextContent(content=f"Error executing tool: {str(e)}")])
                        tool_results.append(res)
                        self.state.messages.append(res)

            emit(TurnEndEvent(message=current_assistant_message, tool_results=tool_results))

            # Check if we should continue
            if self.options.should_stop_after_turn and self.options.should_stop_after_turn(self.state.messages):
                break
            
            # If no tool calls and no steering/followup, we are done
            has_pending = False
            if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                has_pending = True
            if self.state.steering_queue and not self.state.steering_queue.is_empty():
                has_pending = True
            
            if not tool_calls and not has_pending:
                break
            
            # Handle follow-up queue
            if self.state.follow_up_queue:
                followup_msgs = await self.state.follow_up_queue.drain()
                for msg in followup_msgs:
                    emit(MessageStartEvent(message=msg))
                    emit(MessageEndEvent(message=msg))
                    self.state.messages.append(msg)

        emit(AgentEndEvent(messages=self.state.messages))
        


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


