from __future__ import annotations
from operator_use.message.types import ToolResultContent
import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING, Optional, Callable, Coroutine, Literal
from operator_use.hooks.service import Hooks
from operator_use.engine.types import (
    EmitEvent, TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    ToolExecutionFailureEvent,
    BeforeProviderRequestEvent, AfterProviderResponseEvent, QueueUpdateEvent,
)
from operator_use.inference.types import (
    LLMContext,
    ErrorEvent, EndEvent, TextDeltaEvent, TextEndEvent,
    ThinkingDeltaEvent, ThinkingEndEvent, ToolCallEndEvent, StopReason
)
from operator_use.tool.types import ToolContext, ToolExecutionMode, ToolInvocation, ToolResult
from operator_use.message.types import AssistantMessage, ToolCallContent, Role, Usage, TextContent

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool
    from operator_use.agent.types import AgentContext

from operator_use.engine.types import (
    AgentState,
    Options,
    AgentEvent,
    FollowupQueue,
    SteeringQueue,
    AbortSignal,
)
from operator_use.message.types import LLMMessage, ToolMessage


class Engine:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        system_prompt: Optional[str] = None,
        options: Optional[Options] = None,
        hooks: Optional[Hooks] = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.options = options or Options()
        self._hooks = hooks
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self.tool_context = ToolContext(llm=llm, engine=self, hooks=hooks)
        self.state = AgentState(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            follow_up_queue=FollowupQueue(mode=self.options.followup_mode),
            steering_queue=SteeringQueue(mode=self.options.steering_mode),
        )
        self._signal: asyncio.Event = asyncio.Event()
        self._subscribers: list = []
        # Set by tools that need to trigger a deferred action after the current
        # turn is fully saved (e.g. reboot).  Checked by Agent.invoke() after
        # _run_with_retry() returns — never called mid-turn.
        self._deferred_fn: Callable[[], Coroutine] | None = None

    async def subscribe(self, handler):
        """Register an event handler (sync or async). Returns an unsubscribe callable."""
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    async def steer(self, message: LLMMessage) -> None:
        if self.state.steering_queue:    
            await self.state.steering_queue.enqueue(message)
            if self._hooks:
                await self._hooks.emit(QueueUpdateEvent(
                    queue='steering',
                    message=message,
                    messages=self.state.steering_queue.snapshot(),
                ))

    async def follow_up(self, message: LLMMessage) -> None:
        if self.state.follow_up_queue:
            await self.state.follow_up_queue.enqueue(message)
            if self._hooks:
                await self._hooks.emit(QueueUpdateEvent(
                    queue='followup',
                    message=message,
                    messages=self.state.follow_up_queue.snapshot(),
            ))

    def clear_steering(self) -> None:
        if self.state.steering_queue:
            self.state.steering_queue.clear()

    def clear_follow_up(self) -> None:
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        if self.state.steering_queue:
            self.state.steering_queue.clear()
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()

    def has_pending_messages(self) -> bool:
        steering_has = self.state.steering_queue is not None and not self.state.steering_queue.is_empty()
        followup_has = self.state.follow_up_queue is not None and not self.state.follow_up_queue.is_empty()
        return steering_has or followup_has

    def reset(self) -> None:
        if self.state.follow_up_queue:
            self.state.follow_up_queue.clear()
        if self.state.steering_queue:
            self.state.steering_queue.clear()
        self.state.error_message = None
        self.state.pending_tool_calls.clear()
        self.state.is_streaming = False

    def add_tool(self, tool: Tool) -> None:
        """Dynamically register a tool (e.g. from a connected MCP server)."""
        self.tools.append(tool)
        self._tools[tool.name] = tool

    def remove_tool(self, name: str) -> None:
        """Dynamically unregister a tool by name."""
        self.tools = [t for t in self.tools if t.name != name]
        self._tools.pop(name, None)

    def abort(self) -> None:
        self._signal.set()

    @property
    def is_idle(self) -> bool:
        return not self.state.is_streaming

    async def wait_for_idle(self) -> None:
        while self.state.is_streaming:
            await asyncio.sleep(0.05)

    async def process_events(self, event: AgentEvent) -> None:
        match event:
            case MessageStartEvent(message=message):
                self.state.streaming_message = message
            case MessageUpdateEvent(message=message):
                self.state.streaming_message = message
            case MessageEndEvent(message=message):
                self.state.streaming_message = None
                if message:
                    self.state.messages.append(message)
            case ToolExecutionStartEvent(tool_call=tool_call):
                self.state.pending_tool_calls.add(tool_call.id)
            case ToolExecutionEndEvent(tool_result=tool_result):
                self.state.pending_tool_calls.discard(tool_result.id)
            case AgentErrorEvent(error=error):
                self.state.error_message = error
                
        if self._hooks is not None:
            await self._hooks.emit(event)
        if self.options.on_event is not None:
            await self.options.on_event(event)
        for handler in list(self._subscribers):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result

    # -------------------------------------------------------------------------
    # Tool execution
    # -------------------------------------------------------------------------

    async def _execute(
        self,
        tool_call: ToolCallContent,
        emit: EmitEvent,
        signal: Optional[AbortSignal],
    ) -> ToolResultContent:
        if self.options.should_skip_tool_calls is not None:
            return self.options.should_skip_tool_calls(tool_call)

        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResultContent(
                id=tool_call.id, is_error=True,
                content=f"Tool '{tool_call.name}' not found.", metadata={},
            )

        tool_call.kind = tool.kind
        tool_call.metadata['display_name'] = tool.get_display_name(tool_call.args)
        ok, errors = tool.validate(params=tool_call.args)
        if not ok:
            content = f"Invalid parameters for '{tool_call.name}':\n{chr(10).join(errors)}"
            return ToolResultContent(id=tool_call.id, is_error=True, content=content, metadata={})

        invocation = ToolInvocation(id=tool_call.id, params=tool_call.args, name=tool_call.name)

        # before hook — returning ToolResultContent cancels execution
        if self.options.before_tool_call is not None:
            before_result = await self.options.before_tool_call(invocation, signal)
            if isinstance(before_result, ToolResultContent):
                await emit(ToolExecutionEndEvent(tool_result=before_result))
                return before_result
            elif before_result is not None:
                invocation = before_result

        async def on_update(partial: ToolResult) -> None:
            await emit(ToolExecutionUpdateEvent(partial_tool_result=partial))

        tool_result: ToolResultContent
        try:
            await emit(ToolExecutionStartEvent(tool_call=tool_call))
            raw = await tool.execute(
                invocation=invocation,
                tool_execution_update_callback=on_update,
                signal=signal,
                context=self.tool_context,
            )
            if self.options.after_tool_call is not None:
                raw = await self.options.after_tool_call(invocation, raw, signal) or raw
            tool_result = ToolResultContent(
                id=tool_call.id, is_error=raw.is_error,
                content=raw.content, metadata=raw.metadata,
                terminate=raw.terminate,
                terminate_message=raw.terminate_message,
            )
        except Exception as e:
            error = f"Tool '{tool_call.name}' execution failed:\n{e}"
            tool_result = ToolResultContent(
                id=tool_call.id, is_error=True, content=error, metadata={},
            )
            await emit(ToolExecutionFailureEvent(
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                input=tool_call.args,
                error=error,
            ))

        await emit(ToolExecutionEndEvent(tool_result=tool_result))
        return tool_result

    async def _sequential_execute(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: Optional[AbortSignal],
    ) -> list[ToolResultContent]:
        results = []
        for tc in tool_calls:
            results.append(await self._execute(tc, emit, signal))
        return results

    async def _parallel_execute(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: Optional[AbortSignal],
    ) -> list[ToolResultContent]:
        return list(await asyncio.gather(
            *[self._execute(tc, emit, signal) for tc in tool_calls]
        ))

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCallContent],
        emit: EmitEvent,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResultContent]:
        match self.options.execution_mode:
            case ToolExecutionMode.Parallel:
                return await self._parallel_execute(tool_calls, emit, signal)
            case ToolExecutionMode.Batch:
                results: list[ToolResultContent] = []
                parallel_calls: list[ToolCallContent] = []
                sequential_calls: list[ToolCallContent] = []
                for tc in tool_calls:
                    tool = self._tools.get(tc.name)
                    if tool is None:
                        results.append(ToolResultContent(
                            id=tc.id, is_error=True,
                            content=f"Tool '{tc.name}' not found.", metadata={},
                        ))
                        continue
                    if tool.execution_mode == ToolExecutionMode.Parallel:
                        parallel_calls.append(tc)
                    else:
                        sequential_calls.append(tc)
                if parallel_calls:
                    results.extend(await self._parallel_execute(parallel_calls, emit, signal))
                if sequential_calls:
                    results.extend(await self._sequential_execute(sequential_calls, emit, signal))
                return results
            case _:
                return await self._sequential_execute(tool_calls, emit, signal)

    # -------------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------------

    async def _loop(self, messages: list[LLMMessage], emit: EmitEvent, signal: AbortSignal):
        await emit(AgentStartEvent())

        tool_calls: list[ToolCallContent] = []
        tool_results: list[ToolResultContent] = []
        end_reason: Literal['completed', 'aborted', 'error'] = 'completed'

        try:
            while True:
                await emit(TurnStartEvent())
                message = AssistantMessage()
                tool_calls.clear()

                ctx_messages = list(messages)

                if self.options.get_ephemeral_messages is not None:
                    try:
                        ephemeral = await self.options.get_ephemeral_messages()
                        ctx_messages = ctx_messages + ephemeral
                    except Exception:
                        pass

                if self.options.transform_context is not None:
                    ctx_messages = self.options.transform_context(ctx_messages, signal)

                if signal.is_set():
                    end_reason = 'aborted'
                    await emit(TurnEndEvent(message=message, tool_results=tool_results))
                    break

                await emit(MessageStartEvent(message=message))
                if self._hooks:
                    await self._hooks.emit(BeforeProviderRequestEvent(
                        model=self.llm.model,
                        messages=ctx_messages,
                        options=self.llm.api.options,
                    ))

                # aclosing() ensures the provider stream (and its underlying
                # httpx connection) is torn down deterministically inside this
                # task on cancellation/break — not deferred to the GC asyncgen
                # finalizer, which races loop shutdown and emits
                # "Task was destroyed but it is pending!".
                async with aclosing(self.llm.stream(LLMContext(
                    messages=ctx_messages,
                    tools=self.state.tools,
                    system_prompt=self.state.system_prompt,
                ))) as stream:
                    async for event in stream:
                        match event:
                            case ToolCallEndEvent(tool_call=tool_call):
                                tool_calls.append(tool_call)
                                message.contents.append(tool_call)
                            case TextDeltaEvent(text=text):
                                await emit(MessageUpdateEvent(message=AssistantMessage(contents=[text])))
                            case ThinkingDeltaEvent(thinking=thinking):
                                await emit(MessageUpdateEvent(message=AssistantMessage(contents=[thinking])))
                            case TextEndEvent(text=text):
                                message.contents.append(text)
                            case ThinkingEndEvent(thinking=thinking):
                                message.contents.append(thinking)
                            case ErrorEvent(reason=reason, error=error):
                                message.stop_reason = reason
                                message.error = error
                            case EndEvent() as ev:
                                message.stop_reason = ev.reason
                                message.usage = Usage(
                                    input_tokens=ev.input_tokens,
                                    output_tokens=ev.output_tokens,
                                    cache_read_tokens=ev.cache_read_tokens,
                                    cache_write_tokens=ev.cache_write_tokens,
                                )

                if self._hooks:
                    await self._hooks.emit(AfterProviderResponseEvent(
                        model=self.llm.model,
                        response=message,
                    ))

                match message.stop_reason:
                    case StopReason.Error | StopReason.Abort:
                        # message=None on error/abort is load-bearing: Agent.process_events
                        # uses `if message:` to decide history append, and we must NOT
                        # persist failed turns (see commit 4f388eb). Consumers of
                        # MessageEndEvent must guard for None.
                        await emit(MessageEndEvent(message=None))
                        err_msg = message.error or f"Turn failed with reason: {message.stop_reason.value}"
                        end_reason = 'error'
                        await emit(AgentErrorEvent(error=err_msg))
                        await emit(TurnEndEvent(message=None, tool_results=tool_results))
                        break

                    case StopReason.ToolCalls:
                        await emit(MessageEndEvent(message=message))
                        messages.append(message)
                        tool_results = await self._execute_tool_calls(
                            tool_calls=tool_calls,
                            emit=emit,
                            signal=signal,
                        )
                        tool_message = ToolMessage.from_results(tool_results)
                        await emit(MessageStartEvent(message=tool_message))
                        await emit(MessageEndEvent(message=tool_message))
                        messages.append(tool_message)

                        # If every tool signalled terminate, stop without another LLM call.
                        if tool_results and all(r.terminate for r in tool_results):
                            # Close the tool-use turn with a synthetic assistant message so
                            # history never ends on a tool_result. Otherwise the next user
                            # message lands as tool_use -> tool_result -> user (no assistant
                            # turn between), an out-of-distribution shape that makes the model
                            # emit garbage on the following turn (e.g. after a reboot resume).
                            # Each terminating tool supplies its own closing line via
                            # terminate_message; fall back to the raw result content.
                            closing_text = "\n".join(
                                (r.terminate_message or r.content)
                                for r in tool_results
                                if (r.terminate_message or r.content)
                            )
                            if closing_text:
                                closing = AssistantMessage(contents=[TextContent(content=closing_text)])
                                await emit(MessageStartEvent(message=closing))
                                await emit(MessageEndEvent(message=closing))
                                messages.append(closing)
                            await emit(TurnEndEvent(message=message, tool_results=tool_results))
                            break

                        if signal.is_set():
                            end_reason = 'aborted'
                            await emit(TurnEndEvent(message=message, tool_results=tool_results))
                            break

                        # Drain the live steering queue first, then call the options callback.
                        steering_messages: list[LLMMessage] = []
                        if self.state.steering_queue and not self.state.steering_queue.is_empty():
                            steering_messages.extend(await self.state.steering_queue.dequeue())
                        if self.options.get_steering_messages is not None:
                            steering_messages.extend(self.options.get_steering_messages())
                        for msg in steering_messages:
                            await emit(MessageStartEvent(message=msg))
                            await emit(MessageEndEvent(message=msg))
                            messages.append(msg)

                    case StopReason.Stop:
                        await emit(MessageEndEvent(message=message))
                        messages.append(message)
                        # Drain the live follow-up queue first, then call the options callback.
                        follow_up_messages: list[LLMMessage] = []
                        if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                            follow_up_messages.extend(await self.state.follow_up_queue.dequeue())
                        if self.options.get_follow_up_messages is not None:
                            follow_up_messages.extend(self.options.get_follow_up_messages())

                        if follow_up_messages:
                            for msg in follow_up_messages:
                                await emit(MessageStartEvent(message=msg))
                                await emit(MessageEndEvent(message=msg))
                                messages.append(msg)
                        else:
                            await emit(TurnEndEvent(message=message, tool_results=tool_results))
                            break
                    case _:
                        pass

                await emit(TurnEndEvent(message=message, tool_results=tool_results))

                if self.options.should_stop_after_turn:
                    if self.options.should_stop_after_turn(message, tool_results):
                        break

                tool_results.clear()
        except Exception as e:
            end_reason = 'error'
            await emit(AgentErrorEvent(error=str(e)))

        await emit(AgentEndEvent(messages=messages, reason=end_reason))

    async def run(self, ctx: AgentContext) -> None:
        if isinstance(ctx, list):
            from operator_use.agent.types import AgentContext as _AgentContext
            ctx = _AgentContext(
                system_prompt=self.system_prompt or '',
                messages=ctx,
                tools=self.tools,
            )
        self._signal = asyncio.Event()
        self.state.is_streaming = True
        self.state.system_prompt = ctx.system_prompt
        self.state.tools = ctx.tools
        self._tools = {t.name: t for t in ctx.tools}
        try:
            await self._loop(list(ctx.messages), self.process_events, self._signal)
        finally:
            self.state.is_streaming = False

    async def run_continue(self) -> None:
        if self.state.is_streaming:
            raise RuntimeError("Agent is already processing. Wait for completion before continuing.")

        if not self.state.messages:
            # Allow continue when there are queued follow-up messages even with no history
            if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                follow_up_messages = await self.state.follow_up_queue.dequeue()
                from operator_use.agent.types import AgentContext
                await self.run(AgentContext(
                    system_prompt=self.state.system_prompt or '',
                    messages=follow_up_messages,
                ))
                return
            raise RuntimeError("No messages to continue from")

        last_message = self.state.messages[-1]
        if last_message.role == Role.ASSISTANT:
            if self.state.steering_queue and not self.state.steering_queue.is_empty():
                steering_messages = await self.state.steering_queue.dequeue()
                from operator_use.agent.types import AgentContext
                await self.run(AgentContext(
                    system_prompt=self.state.system_prompt or '',
                    messages=self.state.messages + steering_messages,
                ))
                return

            if self.state.follow_up_queue and not self.state.follow_up_queue.is_empty():
                follow_up_messages = await self.state.follow_up_queue.dequeue()
                from operator_use.agent.types import AgentContext
                await self.run(AgentContext(
                    system_prompt=self.state.system_prompt or '',
                    messages=self.state.messages + follow_up_messages,
                ))
                return

            raise RuntimeError("Cannot continue from message role: assistant")

        await self._loop_continue()

    async def _loop_continue(self) -> None:
        self._signal = asyncio.Event()
        self.state.is_streaming = True
        try:
            await self._loop(self.state.messages, self.process_events, self._signal)
        finally:
            self.state.is_streaming = False
