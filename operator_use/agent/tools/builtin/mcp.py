"""MCP tool — list, connect, and disconnect MCP servers from within the agent."""

from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from operator_use.tools import Tool, ToolResult

logger = logging.getLogger(__name__)


class MCPParams(BaseModel):
    """Parameters for the mcp tool."""

    action: Literal["list", "connect", "disconnect"] = Field(
        description=(
            "list       — show all configured MCP servers with connected/disconnected status. "
            "connect    — connect to a server and load its tools into the agent. "
            "disconnect — disconnect from a server and remove its tools from the agent."
        )
    )
    server_name: Optional[str] = Field(
        default=None,
        description="Name of the MCP server to connect or disconnect (required for connect/disconnect).",
    )

    @model_validator(mode="after")
    def check_server_name_required(self) -> MCPParams:
        if self.action in ("connect", "disconnect") and not self.server_name:
            raise ValueError(f"server_name is required for action='{self.action}'")
        return self


@Tool(
    name="mcp",
    description=(
        "Manage MCP (Model Context Protocol) server connections.\n\n"
        "- action='list'                              → show all configured servers and their status\n"
        "- action='connect', server_name='tavily-mcp' → connect and load the server's tools (added to your LLM context)\n"
        "- action='disconnect', server_name='tavily-mcp' → disconnect and remove the server's tools (removed from your LLM context)\n\n"
        "When connected, the MCP server's tools are available to you and included in every LLM call alongside built-in tools."
    ),
    model=MCPParams,
)
async def mcp(
    action: str,
    server_name: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    """MCP tool for managing server connections."""
    from operator_use.mcp import MCPManager

    manager: MCPManager | None = kwargs.get("_mcp_manager")
    agent = kwargs.get("_agent")
    agent_id: str = kwargs.get("_agent_id", "unknown")

    if manager is None:
        return ToolResult.error_result(
            "MCPManager not available. Add mcpServers to config.json to enable MCP."
        )

    if action == "list":
        # Show all configured servers with their connection status
        servers = manager.list_servers()
        if not servers:
            return ToolResult.success_result(
                "No MCP servers configured. Add servers to 'mcpServers' in config.json."
            )
        lines = ["Configured MCP Servers:"]
        for s in servers:
            status = "connected" if s["connected"] else "disconnected"
            tool_info = f" ({s['tool_count']} tools)" if s["connected"] else ""
            agent_status = "  [you: connected]" if s["agent_connected"] else "  [you: disconnected]"
            shared_info = (
                f"  [shared: {s['connection_count']} agent(s)]" if s["connection_count"] > 1 else ""
            )
            lines.append(f"  • {s['name']} [{status}]{tool_info}{agent_status}{shared_info}")
        return ToolResult.success_result("\n".join(lines))

    if action == "connect":
        if manager.is_connected(agent_id, server_name):
            return ToolResult.error_result(f"You are already connected to '{server_name}'.")
        try:
            tools = await manager.connect(agent_id, server_name)
        except ValueError as e:
            return ToolResult.error_result(str(e))
        except Exception as e:
            logger.exception(f"Failed to connect to MCP server '{server_name}'")
            return ToolResult.error_result(f"Failed to connect to '{server_name}': {e}")

        if agent is not None:
            registered, skipped = [], []
            for tool in tools:
                try:
                    agent.tool_register.register(tool)
                    registered.append(tool.name)
                except ValueError:
                    skipped.append(tool.name)
            lines = [f"Connected to '{server_name}'. Loaded {len(registered)} tool(s):"]
            for name in registered:
                lines.append(f"  • {name}")
            if skipped:
                lines.append(f"Skipped (already registered): {', '.join(skipped)}")
            return ToolResult.success_result("\n".join(lines))
        else:
            return ToolResult.success_result(
                f"Connected to '{server_name}' ({len(tools)} tools). No agent available to register them."
            )

    if action == "disconnect":
        if not manager.is_connected(agent_id, server_name):
            return ToolResult.error_result(f"You are not connected to '{server_name}'.")
        try:
            tool_names = await manager.disconnect(agent_id, server_name)
        except Exception as e:
            logger.exception(f"Failed to disconnect from MCP server '{server_name}'")
            return ToolResult.error_result(f"Failed to disconnect from '{server_name}': {e}")

        if agent is not None:
            for name in tool_names:
                try:
                    agent.tool_register.unregister(name)
                except ValueError:
                    pass  # Already gone — that's fine

        lines = [f"Disconnected from '{server_name}'. Removed {len(tool_names)} tool(s):"]
        for name in tool_names:
            lines.append(f"  • {name}")
        return ToolResult.success_result("\n".join(lines))

    return ToolResult.error_result(f"Unknown action '{action}'")
