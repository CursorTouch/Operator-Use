from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class LsSchema(BaseModel):
    path: str = Field(
        default=".",
        description="Absolute path or path relative to the current working directory. Omit to list the current directory.",
    )

class LsTool(Tool):
    def __init__(self):
        super().__init__(
            name="ls",
            description="List files and subdirectories inside a directory. Directories are shown first, then files, both sorted alphabetically.",
            schema=LsSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        path_str = params.get("path", ".")
        resolved_path = Path(path_str).resolve()

        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"Directory not found: {resolved_path}")

        if not resolved_path.is_dir():
            return ToolResult.error(id=invocation.id, content=f"Path is not a directory: {resolved_path}")

        try:
            items = sorted(resolved_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to list directory: {resolved_path}. {e}")

        if not items:
            return ToolResult.ok(id=invocation.id, content=f"Directory is empty: {resolved_path}")

        def _human_size(n: int) -> str:
            for unit in ("B", "K", "M", "G", "T"):
                if n < 1024:
                    return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
                n /= 1024
            return f"{n:.1f}P"

        lines = []
        total_bytes = 0
        for item in items:
            try:
                stat = item.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%b %d %H:%M")
                if item.is_dir():
                    lines.append(f"{'drwxr-xr-x':10s}  {'':>6}  {mtime}  {item.name}/")
                else:
                    size = stat.st_size
                    total_bytes += size
                    lines.append(f"{'-rw-r--r--':10s}  {_human_size(size):>6}  {mtime}  {item.name}")
            except Exception:
                lines.append(f"{'?????????':10s}  {'':>6}  {'':12s}  {item.name}")

        header = f"total {_human_size(total_bytes)}"
        return ToolResult.ok(id=invocation.id, content=header + "\n" + "\n".join(lines))

tool = LsTool()
