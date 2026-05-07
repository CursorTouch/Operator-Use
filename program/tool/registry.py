from __future__ import annotations
from program.tool.types import Tool, ToolInvocation, ToolResult, OnUpdateCallback, AbortSignal
from typing import Any, Optional


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

    async def execute(self, name: str, params: dict[str, Any], on_update: Optional[OnUpdateCallback] = None, signal: Optional[AbortSignal] = None) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult.error(content=f"Tool '{name}' not found.")

        is_valid, errors = tool.validate(params=params)
        if not is_valid:
            content = f"Invalid parameters:\n{chr(10).join(errors)}"
            return ToolResult.error(content=content)

        try:
            invocation = ToolInvocation(params=params)
            return await tool.execute(invocation=invocation, on_update=on_update, signal=signal)
        except Exception as e:
            content = f"Tool '{name}' execution failed:\nError:\n{str(e)}"
            return ToolResult.error(content=content)
