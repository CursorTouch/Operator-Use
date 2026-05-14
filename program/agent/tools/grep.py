from __future__ import annotations
import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

MAX_TOOL_OUTPUT_LENGTH = 100000

class GrepSchema(BaseModel):
    pattern: str = Field(
        ...,
        description="Search pattern (regex or literal string)",
    )
    path: Optional[str] = Field(
        default=".",
        description="Directory or file to search (default: current directory)",
    )
    glob: Optional[str] = Field(
        default=None,
        description="Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'",
    )
    ignore_case: Optional[bool] = Field(
        default=False,
        description="Case-insensitive search (default: false)",
    )
    literal: Optional[bool] = Field(
        default=False,
        description="Treat pattern as literal string instead of regex (default: false)",
    )
    context: Optional[int] = Field(
        default=0,
        description="Number of lines to show before and after each match (default: 0)",
    )
    limit: Optional[int] = Field(
        default=100,
        description="Maximum number of matches to return (default: 100)",
    )

class GrepTool(Tool):
    def __init__(self):
        super().__init__(
            name="grep",
            description="Search for text patterns in files. Supports regex or literal string matching with optional context lines.",
            schema=GrepSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        pattern = params.get("pattern")
        path_str = params.get("path", ".")
        glob_pattern = params.get("glob")
        ignore_case = params.get("ignore_case", False)
        literal = params.get("literal", False)
        context = params.get("context", 0)
        limit = params.get("limit", 100)

        if not pattern:
            return ToolResult.error(id=invocation.id, content="Parameter 'pattern' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"Path not found: {resolved_path}")

        # Collect files to search
        files_to_search = []
        if resolved_path.is_file():
            files_to_search = [resolved_path]
        else:
            if glob_pattern:
                try:
                    files_to_search = list(resolved_path.glob(glob_pattern))
                except Exception as e:
                    return ToolResult.error(id=invocation.id, content=f"Invalid glob pattern: {glob_pattern}. {e}")
            else:
                files_to_search = list(resolved_path.rglob("*"))
            files_to_search = [f for f in files_to_search if f.is_file()]

        if not files_to_search:
            return ToolResult.ok(id=invocation.id, content="No files found matching the search criteria.")

        # Compile regex if not literal
        flags = re.IGNORECASE if ignore_case else 0
        try:
            if literal:
                pattern_str = re.escape(pattern)
            else:
                pattern_str = pattern
            regex = re.compile(pattern_str, flags)
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Invalid regex pattern: {pattern}. {e}")

        matches = []
        match_count = 0

        for file_path in files_to_search:
            if match_count >= limit:
                break

            try:
                with open(file_path, "rb") as f:
                    chunk = f.read(1024)
                    if b'\x00' in chunk:
                        continue

                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    if match_count >= limit:
                        break

                    start_line = max(0, line_num - context - 1)
                    end_line = min(len(lines), line_num + context)
                    context_lines = lines[start_line:end_line]

                    match_block = f"{file_path}:{line_num}: {line.rstrip()}"
                    if context > 0:
                        match_block += "\n"
                        for ctx_line_num in range(start_line + 1, end_line + 1):
                            prefix = ">" if ctx_line_num == line_num else " "
                            match_block += f"{prefix} {ctx_line_num}: {context_lines[ctx_line_num - start_line - 1].rstrip()}\n"

                    matches.append(match_block)
                    match_count += 1

        if not matches:
            return ToolResult.ok(id=invocation.id, content=f"No matches found for pattern: {pattern}")

        content = "\n".join(matches)
        if len(content) > MAX_TOOL_OUTPUT_LENGTH:
            content = content[:MAX_TOOL_OUTPUT_LENGTH] + "\n... [Output Truncated]"

        return ToolResult.ok(id=invocation.id, content=content)
