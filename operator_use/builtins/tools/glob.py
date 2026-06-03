"""glob — Find files matching a glob pattern (recursive wildcards supported)."""
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class GlobSchema(BaseModel):
    """Input schema for glob; validates pattern and optional path/limit."""
    pattern: str = Field(
        ...,
        description="Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'",
    )
    path: Optional[str] = Field(
        default=".",
        description="Directory to search in (default: current directory)",
    )
    limit: Optional[int] = Field(
        default=1000,
        description="Maximum number of results (default: 1000)",
    )

class GlobTool(Tool):
    """Find files matching a glob pattern with optional result limit."""

    def __init__(self):
        super().__init__(
            name="glob",
            description="Find files matching a glob pattern. Supports recursive patterns like '**/*.ext'.",
            schema=GlobSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,        )

    def get_display_name(self, args: dict) -> str:
        """Return a human-readable description of the search pattern."""
        pattern = args.get('pattern', '') or ''
        return f"Searching: {pattern}" if pattern else "Searching files"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Match and return files against the glob pattern, respecting the limit."""
        params = invocation.params
        pattern = params.get("pattern")
        path_str = params.get("path", ".")
        limit = params.get("limit", 1000)

        if not pattern:
            return ToolResult.error(id=invocation.id, content="Parameter 'pattern' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"Path not found: {resolved_path}")

        if not resolved_path.is_dir():
            return ToolResult.error(id=invocation.id, content=f"Path is not a directory: {resolved_path}")

        try:
            matches = list(resolved_path.glob(pattern))
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Invalid glob pattern: {pattern}. {e}")

        if not matches:
            return ToolResult.ok(id=invocation.id, content=f"No files found matching pattern: {pattern}")

        matches = sorted(matches)[:limit]
        result_lines = [str(match) for match in matches]
        content = "\n".join(result_lines)

        return ToolResult.ok(id=invocation.id, content=content)

tool = GlobTool()
