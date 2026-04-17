from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.agent.tools.service import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH

__all__ = ["Tool", "ToolResult", "MAX_TOOL_OUTPUT_LENGTH"]


def __getattr__(name: str):
    if name in ("Tool", "ToolResult", "MAX_TOOL_OUTPUT_LENGTH"):
        from operator_use.agent.tools.service import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH
        g = globals()
        g["Tool"] = Tool
        g["ToolResult"] = ToolResult
        g["MAX_TOOL_OUTPUT_LENGTH"] = MAX_TOOL_OUTPUT_LENGTH
        return g[name]
    raise AttributeError(f"module 'operator_use.tools' has no attribute {name!r}")
