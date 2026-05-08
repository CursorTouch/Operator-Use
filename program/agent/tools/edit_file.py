from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class Edit(BaseModel):
    old_content: str = Field(
        ...,
        description="Exact text to find.",
    )
    new_content: str = Field(
        ..., description="The replacement text."
    )

class EditFileArgs(BaseModel):
    path: str = Field(..., description="Absolute path or path relative to the current working directory.")
    edits: list[Edit] = Field(
        ...,
        description="One or more edits to apply in order.",
    )

class EditFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="edit_file",
            description="Edit a file by replacing exact chunks of text. Pass one or more {old_content, new_content} pairs.",
            schema=EditFileArgs,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        path_str = params.get("path")
        edits_raw = params.get("edits", [])
        
        if not path_str:
             return ToolResult.error(id=invocation.id, content="Parameter 'path' is required.")

        resolved_path = Path(path_str).resolve()
        if not resolved_path.exists():
            return ToolResult.error(id=invocation.id, content=f"File not found: {resolved_path}")
        
        try:
            content = resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to read file: {resolved_path}. {e}")

        # Pydantic might have already validated this into objects if using model_validate
        # but the params passed to execute are often raw dicts.
        for i, entry in enumerate(edits_raw):
            if isinstance(entry, dict):
                old = entry.get("old_content")
                new = entry.get("new_content")
            else:
                old = entry.old_content
                new = entry.new_content
                
            if old is None:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: old_content is required.")
            if old not in content:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: old_content not found in {resolved_path}. Ensure exact match.")
            
            count = content.count(old)
            if count > 1:
                return ToolResult.error(id=invocation.id, content=f"Edit #{i + 1}: old_content matches {count} locations. Be more specific.")
            
            content = content.replace(old, new, 1)

        try:
            resolved_path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to write file: {resolved_path}. {e}")

        return ToolResult.ok(id=invocation.id, content=f"Applied {len(edits_raw)} edit(s) to {resolved_path}.")
