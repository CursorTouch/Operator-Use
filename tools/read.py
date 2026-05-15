from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

MAX_TOOL_OUTPUT_LENGTH = 100000 

class ReadSchema(BaseModel):
    path: str = Field(
        ...,
        description="Absolute path or path relative to the current working directory.",
    )
    offset: int = Field(
        default=0,
        description="0-based line offset to start reading from.",
    )
    limit: int | None = Field(
        default=None,
        description="Maximum number of lines to read.",
    )

class ReadTool(Tool):
    def __init__(self):
        super().__init__(
            name="read",
            description="Read a text file and return its contents with line numbers. Use offset/limit to read a slice of a large file.",
            schema=ReadSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        path_str = params.get("path")
        offset = params.get("offset", 0)
        limit = params.get("limit")
        
        if not path_str:
             return ToolResult.error(id=invocation.id, content="Parameter 'path' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"File not found: {resolved_path}")

        if not resolved_path.is_file():
            return ToolResult.error(id=invocation.id, content=f"Path is not a file: {resolved_path}")

        try:
            # Simple binary check
            with open(resolved_path, "rb") as f:
                chunk = f.read(1024)
                if b'\x00' in chunk:
                     return ToolResult.error(id=invocation.id, content=f"Cannot read binary file: {resolved_path}")
            
            with open(resolved_path, "r", encoding="utf-8") as file:
                lines = file.readlines()
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to read file: {resolved_path}. {e}")

        total_lines = len(lines)
        start_idx = max(0, offset)
        end_idx = total_lines if limit is None else min(total_lines, start_idx + limit)
        selected_lines = lines[start_idx:end_idx]

        max_line_num_width = len(str(end_idx))
        numbered_lines = [
            f"{str(i + 1).rjust(max_line_num_width)} | {line.rstrip(chr(10) + chr(13))}" 
            for i, line in enumerate(selected_lines, start=start_idx)
        ]
        content = "\n".join(numbered_lines)

        if len(content) > MAX_TOOL_OUTPUT_LENGTH:
            content = content[:MAX_TOOL_OUTPUT_LENGTH] + "\n... [Output Truncated]"
        return ToolResult.ok(id=invocation.id, content=content)
