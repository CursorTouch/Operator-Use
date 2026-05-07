from __future__ import annotations
from typing import Any
from program.tool.types import Tool, ToolInvocation, ToolResult


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

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(content=f"Tool '{name}' not found.", is_error=True)

        ok, errors = tool.validate(args)
        if not ok:
            return ToolResult(content="\n".join(errors), is_error=True)

        try:
            invocation = ToolInvocation(params=args)
            return await tool.execute(invocation)
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)
