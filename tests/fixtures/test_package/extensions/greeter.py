"""Greeter extension — registers a tool and a session_start handler."""
from pydantic import BaseModel
from program.extension.types import ToolDefinition
from program.tool.types import ToolResult


class GreetParams(BaseModel):
    name: str


async def _greet(params: GreetParams, invocation, ctx) -> ToolResult:
    return ToolResult.ok(invocation.id, f"Hello, {params.name}!")


def extension(api):
    api.register_tool(ToolDefinition(
        name="greet",
        description="Greet someone by name.",
        parameters=GreetParams,
        execute=_greet,
    ))

    api.on("session_start", lambda event, ctx: None)
