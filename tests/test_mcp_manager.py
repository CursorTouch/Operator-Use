"""Tests for MCP Manager and reference counting."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from operator_use.mcp.manager import MCPManager
from operator_use.mcp.tool import MCPTool
from operator_use.config.service import MCPServerConfig


@pytest.fixture
def mcp_config():
    """Create sample MCP server configs."""
    return [
        MCPServerConfig(
            name="server_1",
            transport="stdio",
            command="uvx",
            args=["mcp-server-filesystem"],
        ),
        MCPServerConfig(
            name="server_2",
            transport="http",
            url="http://localhost:3000",
        ),
    ]


@pytest.fixture
def manager(mcp_config):
    """Create MCPManager instance."""
    return MCPManager(mcp_config)


class TestMCPManagerReferenceCounting:
    """Test reference counting for multi-agent scenarios."""

    def test_is_connected_per_agent(self, manager):
        """Test per-agent connection tracking."""
        # Initially no connections
        assert not manager.is_connected("agent_a", "server_1")
        assert not manager.is_connected("agent_b", "server_1")

        # Simulate Agent A connecting
        manager._agent_connections["agent_a"] = {"server_1"}
        manager._connection_count["server_1"] = 1

        assert manager.is_connected("agent_a", "server_1")
        assert not manager.is_connected("agent_b", "server_1")

        # Simulate Agent B connecting
        manager._agent_connections["agent_b"] = {"server_1"}
        manager._connection_count["server_1"] = 2

        assert manager.is_connected("agent_a", "server_1")
        assert manager.is_connected("agent_b", "server_1")

    def test_is_server_connected(self, manager):
        """Test server connection state (regardless of agents)."""
        assert not manager.is_server_connected("server_1")

        # Simulate connection
        manager._connection_count["server_1"] = 1
        assert manager.is_server_connected("server_1")

        # Multiple agents connected
        manager._connection_count["server_1"] = 3
        assert manager.is_server_connected("server_1")

        # Last agent disconnects
        manager._connection_count["server_1"] = 0
        assert not manager.is_server_connected("server_1")

    def test_list_servers_all(self, manager):
        """Test listing all servers without agent filter."""
        manager._connection_count["server_1"] = 2
        manager._connection_count["server_2"] = 0
        manager._tools["server_1"] = [MagicMock(name="tool_a"), MagicMock(name="tool_b")]

        servers = manager.list_servers(agent_id=None)

        assert len(servers) == 2
        assert servers[0]["name"] == "server_1"
        assert servers[0]["connection_count"] == 2
        assert servers[0]["tool_count"] == 2
        assert servers[1]["name"] == "server_2"
        assert servers[1]["connection_count"] == 0

    def test_list_servers_per_agent(self, manager):
        """Test listing only servers an agent is connected to."""
        manager._agent_connections["agent_a"] = {"server_1"}
        manager._agent_connections["agent_b"] = {"server_2"}
        manager._connection_count["server_1"] = 1
        manager._connection_count["server_2"] = 1
        manager._tools["server_1"] = [MagicMock(name="tool_a")]
        manager._tools["server_2"] = [MagicMock(name="tool_b")]

        # Agent A should only see server_1
        servers_a = manager.list_servers(agent_id="agent_a")
        assert len(servers_a) == 1
        assert servers_a[0]["name"] == "server_1"

        # Agent B should only see server_2
        servers_b = manager.list_servers(agent_id="agent_b")
        assert len(servers_b) == 1
        assert servers_b[0]["name"] == "server_2"

    @pytest.mark.asyncio
    async def test_connect_first_agent_opens_connection(self, manager):
        """Test that first agent actually opens the connection."""
        agent_id = "agent_a"
        server_name = "server_1"

        # Mock the _open_client to avoid actual connection
        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "Test"
        mock_tool.inputSchema = {"type": "object"}

        # fastmcp Client.list_tools() returns a list directly
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch.object(
            MCPManager, "_open_client", new_callable=AsyncMock, return_value=mock_client
        ):
            # Initially count is 0
            assert manager._connection_count.get(server_name, 0) == 0

            # Agent A connects
            tools = await manager.connect(agent_id, server_name)

            # Connection opened
            assert manager._connection_count[server_name] == 1
            assert manager.is_connected(agent_id, server_name)
            assert len(tools) == 1
            assert server_name in manager._clients

    @pytest.mark.asyncio
    async def test_connect_second_agent_reuses_connection(self, manager):
        """Test that second agent reuses the connection."""
        server_name = "server_1"

        # Set up first agent's connection
        mock_client = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "Test"
        mock_tool.inputSchema = {"type": "object"}

        # fastmcp Client.list_tools() returns a list directly
        mock_client.list_tools = AsyncMock(return_value=[mock_tool])

        with patch.object(
            MCPManager, "_open_client", new_callable=AsyncMock, return_value=mock_client
        ):
            # Agent A connects
            await manager.connect("agent_a", server_name)
            assert manager._connection_count[server_name] == 1
            client_count_1 = len(manager._clients)

            # Agent B connects to same server
            await manager.connect("agent_b", server_name)

            # Should reuse connection (no new client opened)
            assert manager._connection_count[server_name] == 2
            assert len(manager._clients) == client_count_1  # Same number of clients
            assert manager.is_connected("agent_a", server_name)
            assert manager.is_connected("agent_b", server_name)

    @pytest.mark.asyncio
    async def test_disconnect_second_agent_keeps_server_alive(self, manager):
        """Test that server stays alive when one of two agents disconnects."""
        server_name = "server_1"

        # Set up two agents connected
        manager._connection_count[server_name] = 2
        manager._agent_connections["agent_a"] = {server_name}
        manager._agent_connections["agent_b"] = {server_name}

        manager._tools[server_name] = [MagicMock(name="tool")]

        # Mock client to avoid actual closing
        mock_client = AsyncMock()
        mock_client.__aexit__ = AsyncMock(return_value=None)
        manager._clients[server_name] = mock_client

        # Agent A disconnects
        await manager.disconnect("agent_a", server_name)

        # Server should still be alive
        assert manager._connection_count[server_name] == 1
        assert not manager.is_connected("agent_a", server_name)
        assert manager.is_connected("agent_b", server_name)
        assert server_name in manager._clients  # Still there!
        mock_client.__aexit__.assert_not_called()  # Not closed

    @pytest.mark.asyncio
    async def test_disconnect_last_agent_kills_server(self, manager):
        """Test that server is killed when last agent disconnects."""
        server_name = "server_1"

        # Only one agent connected
        manager._connection_count[server_name] = 1
        manager._agent_connections["agent_a"] = {server_name}
        manager._tools[server_name] = [MagicMock(name="tool")]

        # Mock client to track if it's closed
        mock_client = AsyncMock()
        mock_client.__aexit__ = AsyncMock(return_value=None)
        manager._clients[server_name] = mock_client

        # Agent A disconnects
        await manager.disconnect("agent_a", server_name)

        # Server should be dead
        assert manager._connection_count[server_name] == 0
        assert not manager.is_server_connected(server_name)
        assert server_name not in manager._clients  # Removed!
        mock_client.__aexit__.assert_called_once()  # Was closed


class TestMCPTool:
    """Test MCPTool schema generation."""

    def test_json_schema_returns_mcp_schema(self):
        """Test that json_schema returns MCP's inputSchema directly."""
        mock_client = MagicMock()
        input_schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        }

        tool = MCPTool(
            server_name="filesystem",
            mcp_tool_name="read_file",
            description="Read a file",
            input_schema=input_schema,
            client=mock_client,
        )

        schema = tool.json_schema
        assert schema["name"] == "mcp_filesystem_read_file"
        assert schema["description"] == "Read a file"
        assert schema["parameters"] == input_schema

    def test_tool_name_namespacing(self):
        """Test that tool names are properly namespaced."""
        mock_client = MagicMock()

        tool = MCPTool(
            server_name="github",
            mcp_tool_name="create_issue",
            description="Create a GitHub issue",
            input_schema={"type": "object"},
            client=mock_client,
        )

        assert tool.name == "mcp_github_create_issue"

    @pytest.mark.asyncio
    async def test_ainvoke_strips_extensions(self):
        """Test that ainvoke strips extension kwargs before calling tool."""
        mock_client = AsyncMock()
        # fastmcp Client.call_tool() returns list[Content] directly
        mock_content = MagicMock()
        mock_content.text = "result"
        mock_client.call_tool = AsyncMock(return_value=[mock_content])

        tool = MCPTool(
            server_name="test",
            mcp_tool_name="tool",
            description="Test",
            input_schema={"type": "object"},
            client=mock_client,
        )

        # Call with extensions + real params
        result = await tool.ainvoke(
            param1="value1",
            _agent=MagicMock(),
            _workspace="/path",
            _mcp_manager=MagicMock(),
        )

        # Should only pass param1 to client.call_tool
        mock_client.call_tool.assert_called_once_with("tool", {"param1": "value1"})
        assert result.success
        assert result.output == "result"

    @pytest.mark.asyncio
    async def test_ainvoke_handles_error(self):
        """Test that ainvoke catches errors."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=Exception("Connection lost"))

        tool = MCPTool(
            server_name="test",
            mcp_tool_name="tool",
            description="Test",
            input_schema={"type": "object"},
            client=mock_client,
        )

        result = await tool.ainvoke(param="value")

        assert not result.success
        assert "Connection lost" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
