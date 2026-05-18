"""mcp tool — list, connect, and disconnect MCP servers at runtime.

One MCPBuiltinTool instance is created per session agent so it holds
a stable reference to that session's Engine for dynamic tool registration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from program.engine.service import Engine
    from program.mcp.manager import MCPManager

logger = logging.getLogger(__name__)


class _MCPSchema(BaseModel):
    action: Literal['list', 'connect', 'disconnect'] = Field(
        description=(
            'list       — show all configured MCP servers and their connection status.\n'
            'connect    — connect to a server and load its tools into your context.\n'
            'disconnect — disconnect from a server and remove its tools.'
        )
    )
    server_name: str | None = Field(
        default=None,
        description='Name of the MCP server (required for connect/disconnect).',
    )

    @model_validator(mode='after')
    def _require_server_name(self) -> _MCPSchema:
        if self.action in ('connect', 'disconnect') and not self.server_name:
            raise ValueError(f"server_name is required for action='{self.action}'")
        return self


class MCPBuiltinTool(Tool):
    """Per-session tool for managing MCP server connections."""

    def __init__(self, manager: MCPManager | None, engine: Engine, agent_id: str) -> None:
        super().__init__(
            name='mcp',
            description=(
                'Manage MCP (Model Context Protocol) server connections.\n\n'
                "  list                              — show all configured servers and status\n"
                "  connect, server_name='<name>'     — connect and load the server's tools\n"
                "  disconnect, server_name='<name>'  — disconnect and remove the server's tools\n\n"
                'When connected, MCP tools are available alongside built-in tools.'
            ),
            schema=_MCPSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._manager = manager
        self._engine = engine
        self._agent_id = agent_id

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
    ) -> ToolResult:
        if self._manager is None:
            return ToolResult.error(
                id=invocation.id,
                content='MCP is not configured. Add mcpServers to settings.json to enable it.',
            )

        action = invocation.params.get('action')
        server_name = invocation.params.get('server_name')

        match action:
            case 'list':
                servers = self._manager.list_servers()
                if not servers:
                    return ToolResult.ok(
                        id=invocation.id,
                        content='No MCP servers configured. Add them to mcpServers in settings.json.',
                    )
                lines = ['Configured MCP servers:']
                for s in servers:
                    status = 'active' if s['active'] else 'inactive'
                    tools_info = f" ({s['tool_count']} tools)" if s['active'] else ''
                    connected = '  [you: connected]' if s['agent_connected'] else '  [you: disconnected]'
                    shared = f"  [shared by {s['shared_by']} session(s)]" if s['shared_by'] > 1 else ''
                    lines.append(f"  • {s['name']} [{status}]{tools_info}{connected}{shared}")
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'connect':
                if self._manager.is_connected(self._agent_id, server_name):
                    return ToolResult.error(
                        id=invocation.id,
                        content=f"Already connected to {server_name!r}.",
                    )
                try:
                    tools = await self._manager.connect(self._agent_id, server_name)
                except ValueError as exc:
                    return ToolResult.error(id=invocation.id, content=str(exc))
                except Exception as exc:
                    logger.exception('Failed to connect to MCP server %r', server_name)
                    return ToolResult.error(id=invocation.id, content=f"Failed to connect to {server_name!r}: {exc}")

                registered, skipped = [], []
                for t in tools:
                    if t.name in self._engine._tools:
                        skipped.append(t.name)
                    else:
                        self._engine.add_tool(t)
                        registered.append(t.name)

                lines = [f"Connected to {server_name!r}. Loaded {len(registered)} tool(s):"]
                for name in registered:
                    lines.append(f'  • {name}')
                if skipped:
                    lines.append(f"Skipped (already registered): {', '.join(skipped)}")
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'disconnect':
                if not self._manager.is_connected(self._agent_id, server_name):
                    return ToolResult.error(
                        id=invocation.id,
                        content=f"Not connected to {server_name!r}.",
                    )
                try:
                    tool_names = await self._manager.disconnect(self._agent_id, server_name)
                except Exception as exc:
                    logger.exception('Failed to disconnect from MCP server %r', server_name)
                    return ToolResult.error(id=invocation.id, content=f"Failed to disconnect from {server_name!r}: {exc}")

                for name in tool_names:
                    self._engine.remove_tool(name)

                lines = [f"Disconnected from {server_name!r}. Removed {len(tool_names)} tool(s):"]
                for name in tool_names:
                    lines.append(f'  • {name}')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action {action!r}.")
