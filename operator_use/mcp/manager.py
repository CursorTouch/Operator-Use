"""MCP Manager — handles MCP server connection lifecycle."""

import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from operator_use.mcp.tool import MCPTool

if TYPE_CHECKING:
    from operator_use.config.service import MCPServerConfig
    from mcp import ClientSession

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages MCP server connections and tool lifecycle.

    Supports multi-agent usage with reference counting:
    - Shared sessions: if Agent A connects to MCP 1, Agent B can reuse the same connection
    - Per-agent visibility: Agent A only sees tools if it explicitly connected
    - Automatic cleanup: server killed only when last agent disconnects
    """

    def __init__(self, server_configs: "list[MCPServerConfig]"):
        # Map server_name -> MCPServerConfig
        self._configs: dict[str, "MCPServerConfig"] = {c.name: c for c in server_configs}
        # Map server_name -> AsyncExitStack (keeps context managers alive)
        self._stacks: dict[str, AsyncExitStack] = {}
        # Map server_name -> list[MCPTool] (tools available from that server)
        self._tools: dict[str, list[MCPTool]] = {}
        # Map server_name -> int (how many agents are connected)
        self._connection_count: dict[str, int] = {}
        # Map agent_id -> set[server_name] (which servers this agent is connected to)
        self._agent_connections: dict[str, set[str]] = {}

    def is_connected(self, agent_id: str, server_name: str) -> bool:
        """Check if a specific agent is connected to a server."""
        return server_name in self._agent_connections.get(agent_id, set())

    def is_server_connected(self, server_name: str) -> bool:
        """Check if ANY agent is connected to a server (internal use)."""
        return self._connection_count.get(server_name, 0) > 0

    def list_servers(self, agent_id: str | None = None) -> list[dict]:
        """Return status info for configured servers.

        If agent_id is provided, show only servers this agent can see.
        Otherwise, show all servers with their connection counts.
        """
        result = []
        for name, cfg in self._configs.items():
            if agent_id is not None:
                # Only show servers this agent is explicitly connected to
                if name not in self._agent_connections.get(agent_id, set()):
                    continue

            result.append({
                "name": name,
                "transport": cfg.transport,
                "connected": self.is_server_connected(name),
                "agent_connected": self.is_connected(agent_id, name) if agent_id else None,
                "tool_count": len(self._tools.get(name, [])),
                "connection_count": self._connection_count.get(name, 0),  # how many agents
            })
        return result

    async def connect(self, agent_id: str, server_name: str) -> list[MCPTool]:
        """Connect an agent to an MCP server and return its tools.

        If another agent is already connected to the same server,
        reuses the connection (reference counting).
        """
        # Check if this agent is already connected
        if self.is_connected(agent_id, server_name):
            logger.info(f"Agent {agent_id} already connected to MCP server '{server_name}'")
            return self._tools.get(server_name, [])

        cfg = self._configs.get(server_name)
        if cfg is None:
            raise ValueError(f"No MCP server config found for '{server_name}'")

        # If first agent connecting to this server, open the actual connection
        is_first_connection = self._connection_count.get(server_name, 0) == 0

        if is_first_connection:
            stack = AsyncExitStack()
            try:
                session = await self._open_session(stack, cfg)
                await session.initialize()
                tools_result = await session.list_tools()
            except Exception:
                await stack.aclose()
                raise

            mcp_tools = [
                MCPTool(
                    server_name=server_name,
                    mcp_tool_name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema,
                    session=session,
                )
                for t in tools_result.tools
            ]

            self._stacks[server_name] = stack
            self._tools[server_name] = mcp_tools
            logger.info(f"MCP server opened | server={server_name} tools={len(mcp_tools)}")

        # Track that this agent is now connected
        if agent_id not in self._agent_connections:
            self._agent_connections[agent_id] = set()
        self._agent_connections[agent_id].add(server_name)
        self._connection_count[server_name] = self._connection_count.get(server_name, 0) + 1

        logger.info(
            f"Agent {agent_id} connected to MCP '{server_name}' "
            f"(connection count: {self._connection_count[server_name]})"
        )
        return self._tools.get(server_name, [])

    async def disconnect(self, agent_id: str, server_name: str) -> list[str]:
        """Disconnect an agent from an MCP server.

        The server is only actually closed when the last agent disconnects.
        Returns list of tool names that were available from this server.
        """
        # Check if agent is connected to this server
        if server_name not in self._agent_connections.get(agent_id, set()):
            raise ValueError(f"Agent {agent_id} is not connected to MCP server '{server_name}'")

        tool_names = [t.name for t in self._tools.get(server_name, [])]

        # Remove agent from connection tracking
        self._agent_connections[agent_id].discard(server_name)
        self._connection_count[server_name] -= 1

        logger.info(
            f"Agent {agent_id} disconnected from MCP '{server_name}' "
            f"(connection count: {self._connection_count[server_name]})"
        )

        # If no more agents connected, actually close the server
        if self._connection_count[server_name] == 0:
            if server_name in self._stacks:
                stack = self._stacks.pop(server_name)
                try:
                    await stack.aclose()
                except (RuntimeError, GeneratorExit) as e:
                    # RuntimeError from asyncio scope issues during cleanup is non-fatal
                    logger.debug(f"Non-fatal error closing MCP stack for '{server_name}': {e}")
                except Exception as e:
                    logger.warning(f"Error closing MCP stack for '{server_name}': {e}")
            self._tools.pop(server_name, None)
            logger.info(f"MCP server closed | server={server_name}")

        return tool_names

    async def disconnect_all(self, agent_id: str | None = None) -> None:
        """Gracefully disconnect all connected servers for an agent.

        If agent_id is provided, disconnect that agent from all its servers.
        If agent_id is None, disconnect ALL agents from ALL servers (full shutdown).
        """
        if agent_id is not None:
            # Disconnect single agent from all its servers
            servers_to_disconnect = list(self._agent_connections.get(agent_id, set()))
            for server_name in servers_to_disconnect:
                try:
                    await self.disconnect(agent_id, server_name)
                except (RuntimeError, GeneratorExit):
                    # Non-fatal async scope errors during cleanup
                    logger.debug(f"Non-fatal error disconnecting agent {agent_id} from '{server_name}'")
                except Exception as e:
                    logger.warning(f"Error disconnecting agent {agent_id} from '{server_name}': {e}")
        else:
            # Full shutdown: disconnect all agents from all servers
            all_agents = list(self._agent_connections.keys())
            for agent in all_agents:
                servers = list(self._agent_connections.get(agent, set()))
                for server_name in servers:
                    try:
                        await self.disconnect(agent, server_name)
                    except (RuntimeError, GeneratorExit):
                        # Non-fatal async scope errors during cleanup
                        logger.debug(f"Non-fatal error disconnecting agent {agent} from '{server_name}'")
                    except Exception as e:
                        logger.warning(f"Error disconnecting agent {agent} from '{server_name}': {e}")

    @staticmethod
    async def _open_session(stack: AsyncExitStack, cfg: "MCPServerConfig") -> "ClientSession":
        """Open the appropriate MCP transport and session, managed by stack."""
        from mcp import ClientSession

        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            if not cfg.command:
                raise ValueError(f"MCP server '{cfg.name}' with transport=stdio requires a command")

            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))

        elif cfg.transport in ("http", "sse"):
            from mcp.client.streamable_http import streamablehttp_client

            if not cfg.url:
                raise ValueError(
                    f"MCP server '{cfg.name}' with transport={cfg.transport} requires a url"
                )

            headers = {}
            if cfg.auth_token:
                headers["Authorization"] = f"Bearer {cfg.auth_token}"

            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(cfg.url, headers=headers)
            )

        else:
            raise ValueError(f"Unknown MCP transport '{cfg.transport}' for server '{cfg.name}'")

        session = await stack.enter_async_context(ClientSession(read, write))
        return session
