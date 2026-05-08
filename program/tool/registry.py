from __future__ import annotations
from program.agent.types import AbortSignal
from program.tool.types import Tool, ToolInvocation, ToolResult, ToolExecutionMode
from program.llm.types import ToolCallContent
from typing import Any, Callable, Optional
import asyncio


OnUpdateFunc = Callable[[ToolResult], None]

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
        on_update: Optional[OnUpdateFunc] = None, 
        signal: Optional[AbortSignal] = None
    ) -> ToolResult:
        tool = self.get(tool_call.name)
        if tool is None:
            return ToolResult.error(id=tool_call.id, content=f"Tool '{tool_call.name}' not found.")

        is_valid, errors = tool.validate(params=tool_call.args)
        if not is_valid:
            content = f"Invalid parameters for '{tool_call.name}':\n{chr(10).join(errors)}"
            return ToolResult.error(id=tool_call.id, content=content)

        try:
            invocation = ToolInvocation(id=tool_call.id, params=tool_call.args)
            return await tool.execute(invocation=invocation, on_update=on_update, signal=signal)
        except Exception as e:
            content = f"Tool '{tool_call.name}' execution failed:\n{str(e)}"
            return ToolResult.error(id=tool_call.id, content=content)

    async def sequential_execute(
        self, 
        calls: list[ToolCallContent], 
        on_update: Optional[OnUpdateFunc] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResult]:
        """Execute multiple tools sequentially."""
        results = []
        for call in calls:
            result = await self.execute(call, on_update=on_update, signal=signal)
            results.append(result)
        return results

    async def parallel_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        on_update: Optional[OnUpdateFunc] = None,
        signal: Optional[AbortSignal] = None,
    ) -> list[ToolResult]:
        """Execute multiple tools in parallel."""
        tasks = []
        for tool_call in tool_calls:
            tasks.append(self.execute(tool_call, on_update=on_update, signal=signal))
        
        results = await asyncio.gather(*tasks)
        return results

    async def batch_execute(
        self, 
        tool_calls: list[ToolCallContent], 
        on_update: Optional[OnUpdateFunc] = None,
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
            tasks = [self.execute(tool_call, on_update=on_update, signal=signal) for tool_call in parallel_calls]
            results.extend(await asyncio.gather(*tasks))

        # 3. Execute Sequential calls one by one
        for tool_call in sequential_calls:
            results.append(await self.execute(tool_call, on_update=on_update, signal=signal))

        return results
