from __future__ import annotations
from program.message.types import ToolResultContent, ToolCallContent
from program.agent.types import AbortSignal, EmitEvent,ToolExecutionStartEvent,ToolExecutionEndEvent,ToolExecutionUpdateEvent
from program.tool.types import Tool, ToolInvocation, ToolExecutionMode
from typing import Any, Optional
from program.agent.types import Options
import asyncio

class ToolRegistry:
    def __init__(self, tools: Optional[list[Tool]] = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def execute(
        self, 
        tool_call: ToolCallContent, 
        options: Optional[Options] = None,
        emit: Optional[EmitEvent] = None,
        signal: Optional[AbortSignal] = None
    ) -> ToolResultContent:

        if options is not None and options.should_skip_tool_calls is not None:
            tool_result = options.should_skip_tool_calls(tool_call)
            return tool_result

        tool = self.get(tool_call.name)
        if tool is None:
            content=f"Tool '{tool_call.name}' not found."
            return ToolResultContent(id=tool_call.id, is_error=True, content=content, metadata={})

        ok, errors = tool.validate(params=tool_call.args)
        if not ok:
            content = f"Invalid parameters for '{tool_call.name}':\n{chr(10).join(errors)}"
            return ToolResultContent(id=tool_call.id, is_error=True, content=content, metadata={})

        try:
            invocation = ToolInvocation(id=tool_call.id, params=tool_call.args)
            if options is not None and options.before_tool_call is not None:
                invocation = options.before_tool_call(invocation, signal)

            emit(ToolExecutionStartEvent(tool_call=tool_call))

            tool_result=await tool.execute(
                invocation=invocation, 
                tool_execution_update_callback=lambda partial_tool_result: emit(ToolExecutionUpdateEvent(partial_tool_result=partial_tool_result)), 
                signal=signal
            )

            if options is not None and options.after_tool_call is not None:
                tool_result = options.after_tool_call(tool_result, signal) or tool_result
            tool_result = ToolResultContent(id=tool_call.id, is_error=tool_result.is_error, content=tool_result.content, metadata=tool_result.metadata)
        except Exception as e:
            content = f"Tool '{tool_call.name}' execution failed:\n{str(e)}"
            tool_result=ToolResultContent(id=tool_call.id, is_error=True, content=content, metadata={})

        finally:
            emit(ToolExecutionEndEvent(tool_result=tool_result))
            return tool_result


    async def sequential_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        options: Optional[Options] = None,
        emit: Optional[EmitEvent] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResultContent]:
        """Execute multiple tools sequentially."""
        results: list[ToolResultContent] = []
        for tool_call in tool_calls:
            result = await self.execute(tool_call, options=options, emit=emit, signal=signal)
            results.append(result)
        return results

    async def parallel_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        options: Optional[Options] = None,
        emit: Optional[EmitEvent] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResultContent]:
        """Execute multiple tools in parallel."""
        tasks: list[asyncio.Future[ToolResultContent]] = []
        for tool_call in tool_calls:
            tasks.append(self.execute(tool_call, options=options, emit=emit, signal=signal))
        
        results = await asyncio.gather(*tasks)
        return results

    async def batch_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        options: Optional[Options] = None,
        emit: Optional[EmitEvent] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResultContent]:
        """Execute multiple tools and return a flat list of results."""
        results: list[ToolResultContent] = []
        parallel_calls: list[ToolCallContent] = []
        sequential_calls: list[ToolCallContent] = []

        # 1. Group calls or handle missing tools immediately
        for tool_call in tool_calls:
            tool = self.get(tool_call.name)
            if tool is None:
                results.append(ToolResultContent(id=tool_call.id, is_error=True, content=f"Tool '{tool_call.name}' not found.", metadata={}))
                continue

            match tool.execution_mode:
                case ToolExecutionMode.Parallel:
                    parallel_calls.append(tool_call)
                case ToolExecutionMode.Sequential:
                    sequential_calls.append(tool_call)

        # 2. Execute Parallel calls concurrently
        if parallel_calls:
            parallel_results = await self.parallel_execute(parallel_calls, options=options, emit=emit, signal=signal)
            results.extend(parallel_results)

        # 3. Execute Sequential calls one by one
        if sequential_calls:
            sequential_results = await self.sequential_execute(sequential_calls, options=options, emit=emit, signal=signal)
            results.extend(sequential_results)

        return results
