from __future__ import annotations
from program.agent.types import AbortSignal, EmitEvent,ToolExecutionStartEvent,ToolExecutionEndEvent,ToolExecutionUpdateEvent
from program.tool.types import Tool, ToolInvocation, ToolResult, ToolExecutionMode
from program.llm.types import ToolCallContent
from typing import Any, Optional
from program.agent.types import Options
import asyncio

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

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
    ) -> ToolResult:
        tool = self.get(tool_call.name)
        if tool is None:
            content=f"Tool '{tool_call.name}' not found."
            return ToolResult.error(id=tool_call.id, content=content)

        ok, errors = tool.validate(params=tool_call.args)
        if not ok:
            content = f"Invalid parameters for '{tool_call.name}':\n{chr(10).join(errors)}"
            return ToolResult.error(id=tool_call.id, content=content)

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
                tool_result = options.after_tool_call(tool_result, signal)
        except Exception as e:
            content = f"Tool '{tool_call.name}' execution failed:\n{str(e)}"
            tool_result=ToolResult.error(id=tool_call.id, content=content)

        finally:
            emit(ToolExecutionEndEvent(tool_result=tool_result))
            return tool_result


    async def sequential_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        options: Optional[Options] = None,
        emit: Optional[EmitEvent] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResult]:
        """Execute multiple tools sequentially."""
        results: list[ToolResult] = []
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
    ) -> list[ToolResult]:
        """Execute multiple tools in parallel."""
        tasks: list[asyncio.Future[ToolResult]] = []
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
    ) -> list[ToolResult]:
        """Execute multiple tools and return a flat list of results."""
        results: list[ToolResult] = []
        parallel_calls: list[ToolCallContent] = []
        sequential_calls: list[ToolCallContent] = []

        # 1. Group calls or handle missing tools immediately
        for tool_call in tool_calls:
            tool = self.get(tool_call.name)
            if tool is None:
                results.append(ToolResult.error(id=tool_call.id, content=f"Tool '{tool_call.name}' not found."))
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
