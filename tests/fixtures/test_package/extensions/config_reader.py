"""Config reader extension — reads its settings via api.config."""
from pydantic import BaseModel
from operator_use.extension.types import ToolDefinition
from operator_use.tool.types import ToolResult

_captured_config = {}


class NoParams(BaseModel):
    pass


async def _get_config(params: NoParams, invocation, ctx) -> ToolResult:
    return ToolResult.ok(invocation.id, str(_captured_config))


def extension(api):
    _captured_config.update(api.config)

    api.register_tool(ToolDefinition(
        name="get_config",
        description="Return the extension config.",
        parameters=NoParams,
        execute=_get_config,
    ))
