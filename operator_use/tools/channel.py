"""Channel management tool — list, status, enable, disable."""

from typing import Literal

from pydantic import BaseModel, Field

from operator_use.agent.tools.service import Tool, ToolResult


class Channel(BaseModel):
    mode: Literal["list", "status", "enable", "disable"] = Field(
        ...,
        description=(
            "list — show all registered channels and their status. "
            "status — check status of a specific channel. "
            "enable — start a stopped channel. "
            "disable — stop a running channel."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Channel name, e.g. 'slack', 'telegram', 'discord'. Required for status, enable, disable.",
    )


@Tool(
    name="channel",
    description="Manage gateway channels. Use 'list' to see all channels, 'status' to check one, 'enable' to start a stopped channel, 'disable' to stop a running channel.",
    model=Channel,
)
async def channel(
    mode: Literal["list", "status", "enable", "disable"],
    name: str | None = None,
    **kwargs,
) -> ToolResult:
    gateway = kwargs.get("_gateway")
    if not gateway:
        return ToolResult.error_result("Gateway not available.")

    if mode == "list":
        channels = gateway.list_channels()
        if not channels:
            return ToolResult.success_result("No channels registered.")
        lines = [f"- {ch.name}: {'running' if ch.running else 'stopped'}" for ch in channels]
        return ToolResult.success_result("\n".join(lines))

    if not name:
        return ToolResult.error_result(f"'name' is required for mode '{mode}'.")

    ch = gateway.get_channel(name)
    if not ch:
        return ToolResult.error_result(f"Channel '{name}' not found.")

    if mode == "status":
        status = "running" if ch.running else "stopped"
        return ToolResult.success_result(f"Channel '{name}' is {status}.")

    if mode == "enable":
        if ch.running:
            return ToolResult.success_result(f"Channel '{name}' is already running.")
        if await gateway.enable_channel(name):
            return ToolResult.success_result(f"Channel '{name}' started.")
        return ToolResult.error_result(f"Failed to start channel '{name}'.")

    if mode == "disable":
        if not ch.running:
            return ToolResult.success_result(f"Channel '{name}' is already stopped.")
        if await gateway.disable_channel(name):
            return ToolResult.success_result(f"Channel '{name}' stopped.")
        return ToolResult.error_result(f"Failed to stop channel '{name}'.")

    return ToolResult.error_result(f"Unknown mode: {mode}")
