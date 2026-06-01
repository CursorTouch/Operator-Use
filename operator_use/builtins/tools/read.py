import zlib
from pathlib import Path
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

MAX_TOOL_OUTPUT_LENGTH = 100000

def _line_hash(line: str, line_num: int) -> str:
    stripped = line.rstrip('\n\r').strip()
    if not any(c.isalnum() for c in stripped):
        return format(line_num % 256, '02x')
    return format(zlib.crc32(stripped.encode('utf-8')) % 256, '02x')

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
            description=(
                "Read a text file and return its contents. Each line is prefixed with "
                "LINE:HASH| (e.g. '5:a3|def hello():') — use these anchors with edit_file "
                "to make precise edits. Use offset/limit to read a slice of a large file."
            ),
            schema=ReadSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )

    def get_display_name(self, args: dict) -> str:
        path = args.get('path', '') or ''
        name = path.rsplit('/', 1)[-1] if path else ''
        return f"Reading: {name}" if name else "Reading file"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
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

        numbered_lines = [
            f"{start_idx + i + 1}:{_line_hash(line, start_idx + i + 1)}|{line.rstrip(chr(10) + chr(13))}"
            for i, line in enumerate(selected_lines)
        ]
        content = "\n".join(numbered_lines)

        if len(content) > MAX_TOOL_OUTPUT_LENGTH:
            content = content[:MAX_TOOL_OUTPUT_LENGTH] + "\n... [Output Truncated]"
        return ToolResult.ok(id=invocation.id, content=content)

tool = ReadTool()
