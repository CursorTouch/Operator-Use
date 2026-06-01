import re
import zlib
from pathlib import Path
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

MAX_TOOL_OUTPUT_LENGTH = 100000


def _file_hash(content: str) -> str:
    """4-hex whole-file hash. Trailing whitespace is stripped before hashing so
    formatter runs don't invalidate the tag."""
    normalized = re.sub(r'[ \t\r]+(?=\n|$)', '', content)
    return format(zlib.crc32(normalized.encode('utf-8')) & 0xFFFF, '04X')


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
                "Read a text file. Returns a header line '¶PATH#TAG' followed by numbered "
                "lines as 'LINE:content'. The TAG is a 4-hex whole-file hash — copy it "
                "exactly into edit_file's file_hash field. Line numbers are 1-based and "
                "refer to the original file; use them as-is in edits."
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

            raw = resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to read file: {resolved_path}. {e}")

        tag = _file_hash(raw)
        lines = raw.splitlines()
        total_lines = len(lines)
        start_idx = max(0, offset)
        end_idx = total_lines if limit is None else min(total_lines, start_idx + limit)
        selected = lines[start_idx:end_idx]

        header = f"¶{resolved_path}#{tag}"
        numbered = "\n".join(f"{start_idx + i + 1}:{line}" for i, line in enumerate(selected))
        output = header + "\n" + numbered

        if len(output) > MAX_TOOL_OUTPUT_LENGTH:
            output = output[:MAX_TOOL_OUTPUT_LENGTH] + "\n... [Output Truncated]"
        return ToolResult.ok(id=invocation.id, content=output)


tool = ReadTool()
