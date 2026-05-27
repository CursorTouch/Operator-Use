from pathlib import Path
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class WriteSchema(BaseModel):
    path: str = Field(
        ...,
        description="Absolute path or path relative to the current working directory.",
    )
    content: str = Field(
        ...,
        description="Full content to write. This replaces the entire file.",
    )
    overwrite: bool = Field(
        default=True,
        description="Set to False to prevent accidentally overwriting an existing file. Default is True, so the file will be overwritten if it exists.",
    )

class WriteTool(Tool):
    def __init__(self):
        super().__init__(
            name="write",
            description="Create a new file or fully overwrite an existing one. Parent directories are created automatically.",
            schema=WriteSchema,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Parallel,        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        path_str = params.get("path")
        content = params.get("content")
        overwrite = params.get("overwrite", True)
        
        if not path_str:
             return ToolResult.error(id=invocation.id, content="Parameter 'path' is required.")
        if content is None:
             return ToolResult.error(id=invocation.id, content="Parameter 'content' is required.")

        resolved_path = Path(path_str).resolve()
        file_exists = resolved_path.exists()
        
        if file_exists and not overwrite:
            return ToolResult.error(id=invocation.id, content=f"File exists and overwrite=False: {resolved_path}")
        
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to write file: {resolved_path}. {e}")
            
        return ToolResult.ok(id=invocation.id, content=f"{'Overwrote' if file_exists else 'Created'} file: {resolved_path}")

tool = WriteTool()
