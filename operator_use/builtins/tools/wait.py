import asyncio
from pydantic import BaseModel, Field
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class WaitSchema(BaseModel):
    seconds: float = Field(
        ...,
        description="Number of seconds to wait (e.g. 5, 2.5).",
        gt=0,
    )

class WaitTool(Tool):
    def __init__(self):
        super().__init__(
            name="wait",
            description="Wait or delay execution for a specific number of seconds.",
            schema=WaitSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        seconds = params.get("seconds")
        
        if seconds is None:
            return ToolResult.error(id=invocation.id, content="Parameter 'seconds' is required.")
            
        try:
            seconds = float(seconds)
        except ValueError:
            return ToolResult.error(id=invocation.id, content="Parameter 'seconds' must be a number.")
            
        await asyncio.sleep(seconds)
        return ToolResult.ok(id=invocation.id, content=f"Successfully waited for {seconds} seconds.")

tool = WaitTool()
