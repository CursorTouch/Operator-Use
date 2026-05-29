"""MCPManager — MCP server connection lifecycle with reference counting.

Multiple gateway session agents can share one physical MCP connection.
A server is started on first connect and killed only when the last agent
disconnects.  Each agent tracks its own connected-server set so tools are
only visible to sessions that explicitly connected.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import Client
    from operator_use.mcp.types import MCPServerConfig
    from operator_use.mcp.tool import MCPTool

logger = logging.getLogger(__name__)


class MCPManager:
    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs: dict[str, MCPServerConfig] = {c.name: c for c in configs}
        self._clients: dict[str, Client] = {}
        self._tools: dict[str, list[MCPTool]] = {}
        self._connection_count: dict[str, int] = {}
        self._agent_connections: dict[str, set[str]] = {}  # agent_id -> set of server names
        # Per-server locks serialise connect/disconnect: both check the ref count
        # and then await (open/close the client), so without this two concurrent
        # first-connects to the same server would each open a client, leaking one.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, server_name: str) -> asyncio.Lock:
        lock = self._locks.get(server_name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[server_name] = lock
        return lock

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_connected(self, agent_id: str, server_name: str) -> bool:
        return server_name in self._agent_connections.get(agent_id, set())

    def is_server_active(self, server_name: str) -> bool:
        return self._connection_count.get(server_name, 0) > 0

    def list_servers(self, agent_id: str | None = None) -> list[dict]:
        result = []
        for name, cfg in self._configs.items():
            if agent_id is not None and name not in self._agent_connections.get(agent_id, set()):
                continue
            result.append({
                'name': name,
                'transport': cfg.transport,
                'active': self.is_server_active(name),
                'agent_connected': self.is_connected(agent_id, name) if agent_id else None,
                'tool_count': len(self._tools.get(name, [])),
                'shared_by': self._connection_count.get(name, 0),
            })
        return result

    def all_server_names(self) -> list[str]:
        return list(self._configs.keys())

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self, agent_id: str, server_name: str) -> list[MCPTool]:
        """Connect agent to a server. Returns its tools. No-op if already connected."""
        from operator_use.mcp.tool import MCPTool as _MCPTool

        async with self._lock_for(server_name):
            if self.is_connected(agent_id, server_name):
                logger.info('Agent %s already connected to MCP server %r', agent_id, server_name)
                return self._tools.get(server_name, [])

            cfg = self._configs.get(server_name)
            if cfg is None:
                raise ValueError(f"No MCP server configured with name {server_name!r}")

            first_connection = self._connection_count.get(server_name, 0) == 0
            if first_connection:
                client = await self._open_client(cfg)
                try:
                    raw_tools = await client.list_tools()
                except Exception:
                    await client.__aexit__(None, None, None)
                    raise

                mcp_tools = [
                    _MCPTool(
                        server_name=server_name,
                        tool_name=t.name,
                        description=t.description or '',
                        input_schema=t.inputSchema,
                        client=client,
                    )
                    for t in raw_tools
                ]
                self._clients[server_name] = client
                self._tools[server_name] = mcp_tools
                logger.info('MCP server %r opened — %d tool(s)', server_name, len(mcp_tools))

            self._agent_connections.setdefault(agent_id, set()).add(server_name)
            self._connection_count[server_name] = self._connection_count.get(server_name, 0) + 1
            logger.info('Agent %s connected to MCP %r (refs=%d)', agent_id, server_name,
                        self._connection_count[server_name])
            return self._tools.get(server_name, [])

    async def disconnect(self, agent_id: str, server_name: str) -> list[str]:
        """Disconnect agent from a server. Returns list of tool names that were removed."""
        async with self._lock_for(server_name):
            if server_name not in self._agent_connections.get(agent_id, set()):
                raise ValueError(f"Agent {agent_id!r} is not connected to {server_name!r}")

            tool_names = [t.name for t in self._tools.get(server_name, [])]
            self._agent_connections[agent_id].discard(server_name)
            self._connection_count[server_name] -= 1

            logger.info('Agent %s disconnected from MCP %r (refs=%d)', agent_id, server_name,
                        self._connection_count[server_name])

            if self._connection_count[server_name] == 0:
                client = self._clients.pop(server_name, None)
                if client is not None:
                    try:
                        await client.__aexit__(None, None, None)
                    except Exception as exc:
                        logger.warning('Error closing MCP client for %r: %s', server_name, exc)
                self._tools.pop(server_name, None)
                logger.info('MCP server %r closed', server_name)

            return tool_names

    async def disconnect_all(self, agent_id: str | None = None) -> None:
        """Disconnect all servers for an agent (or all agents if agent_id is None)."""
        if agent_id is not None:
            for name in list(self._agent_connections.get(agent_id, set())):
                try:
                    await self.disconnect(agent_id, name)
                except Exception as exc:
                    logger.warning('Error disconnecting agent %s from %r: %s', agent_id, name, exc)
        else:
            for aid in list(self._agent_connections):
                for name in list(self._agent_connections.get(aid, set())):
                    try:
                        await self.disconnect(aid, name)
                    except Exception as exc:
                        logger.warning('Error disconnecting agent %s from %r: %s', aid, name, exc)

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _open_client(cfg: MCPServerConfig) -> Client:
        from fastmcp import Client

        if cfg.transport == 'stdio':
            from fastmcp.client.transports import StdioTransport
            if not cfg.command:
                raise ValueError(f"MCP server {cfg.name!r} with transport=stdio requires a command")
            transport = StdioTransport(command=cfg.command, args=cfg.args, env=cfg.env or None)

        elif cfg.transport in ('http', 'sse'):
            if not cfg.url:
                raise ValueError(f"MCP server {cfg.name!r} with transport={cfg.transport} requires a url")
            headers: dict[str, str] = {}
            if cfg.auth_token:
                headers['Authorization'] = f'Bearer {cfg.auth_token}'
            if cfg.transport == 'sse':
                from fastmcp.client.transports import SSETransport
                transport = SSETransport(cfg.url, headers=headers or None)
            else:
                from fastmcp.client.transports import StreamableHttpTransport
                transport = StreamableHttpTransport(cfg.url, headers=headers or None)

        else:
            raise ValueError(f"Unknown MCP transport {cfg.transport!r} for server {cfg.name!r}")

        client = Client(transport)
        await client.__aenter__()
        return client
