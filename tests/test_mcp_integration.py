"""Integration tests for multi-agent MCP scenarios."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from operator_use.mcp.manager import MCPManager
from operator_use.config.service import MCPServerConfig


@pytest.mark.asyncio
async def test_multi_agent_shared_connection_scenario():
    """Integration test: Two agents sharing an MCP server connection."""

    # Setup: Configure two MCP servers
    config = [
        MCPServerConfig(
            name="mcp_1",
            transport="stdio",
            command="uvx",
            args=["mcp-server-filesystem"],
        ),
        MCPServerConfig(
            name="mcp_2",
            transport="http",
            url="http://localhost:3000",
        ),
    ]

    manager = MCPManager(config)

    # Create mock session and tools
    mock_session_1 = AsyncMock()
    mock_tool_1a = MagicMock(name="read_file")
    mock_tool_1a.name = "read_file"
    mock_tool_1a.description = "Read a file"
    mock_tool_1a.inputSchema = {"type": "object"}

    mock_tool_1b = MagicMock(name="write_file")
    mock_tool_1b.name = "write_file"
    mock_tool_1b.description = "Write a file"
    mock_tool_1b.inputSchema = {"type": "object"}

    mock_session_1.initialize = AsyncMock()
    mock_session_1.list_tools = AsyncMock(
        return_value=MagicMock(tools=[mock_tool_1a, mock_tool_1b])
    )

    # --- SCENARIO: Agent A connects to MCP 1 ---
    print("\n[Agent A] Connecting to MCP 1...")
    with patch.object(
        manager, "_open_session", new_callable=AsyncMock, return_value=mock_session_1
    ):
        tools_a = await manager.connect("agent_a", "mcp_1")

    assert manager.is_connected("agent_a", "mcp_1"), "Agent A should be connected"
    assert not manager.is_connected("agent_b", "mcp_1"), "Agent B should NOT be connected"
    assert manager._connection_count["mcp_1"] == 1, "Connection count should be 1"
    assert len(tools_a) == 2, "Agent A should see 2 tools"
    print(f"  [OK] Agent A connected, got {len(tools_a)} tools")
    print(f"  [OK] Connection count: {manager._connection_count['mcp_1']}")

    # --- SCENARIO: Agent B connects to same MCP 1 (reuses connection) ---
    print("\n[Agent B] Connecting to MCP 1...")
    # No new _open_session call should happen
    tools_b = await manager.connect("agent_b", "mcp_1")

    assert manager.is_connected("agent_a", "mcp_1"), "Agent A should still be connected"
    assert manager.is_connected("agent_b", "mcp_1"), "Agent B should be connected"
    assert manager._connection_count["mcp_1"] == 2, "Connection count should be 2"
    assert len(tools_b) == 2, "Agent B should also see 2 tools"
    print(f"  [OK] Agent B connected (reused connection), got {len(tools_b)} tools")
    print(f"  [OK] Connection count: {manager._connection_count['mcp_1']}")

    # --- SCENARIO: Agent A disconnects ---
    print("\n[Agent A] Disconnecting from MCP 1...")
    tool_names_a = await manager.disconnect("agent_a", "mcp_1")

    assert not manager.is_connected("agent_a", "mcp_1"), "Agent A should be disconnected"
    assert manager.is_connected("agent_b", "mcp_1"), "Agent B should still be connected"
    assert manager._connection_count["mcp_1"] == 1, "Connection count should be 1"
    assert "mcp_1" in manager._stacks, "Server should still be alive"
    print(f"  [OK] Agent A disconnected, removed {len(tool_names_a)} tools")
    print(f"  [OK] Connection count: {manager._connection_count['mcp_1']}")
    print("  [OK] Server still running (for Agent B)")

    # --- SCENARIO: Agent B disconnects (kills server) ---
    print("\n[Agent B] Disconnecting from MCP 1...")
    tool_names_b = await manager.disconnect("agent_b", "mcp_1")

    assert not manager.is_connected("agent_a", "mcp_1"), "Agent A should be disconnected"
    assert not manager.is_connected("agent_b", "mcp_1"), "Agent B should be disconnected"
    assert manager._connection_count["mcp_1"] == 0, "Connection count should be 0"
    assert "mcp_1" not in manager._stacks, "Server should be dead"
    print(f"  [OK] Agent B disconnected, removed {len(tool_names_b)} tools")
    print(f"  [OK] Connection count: {manager._connection_count['mcp_1']}")
    print("  [OK] Server CLOSED (no more agents)")

    # --- VERIFY: List servers shows correct state ---
    print("\n[List Servers] Querying connection status...")
    all_servers = manager.list_servers()
    agent_a_servers = manager.list_servers(agent_id="agent_a")
    agent_b_servers = manager.list_servers(agent_id="agent_b")

    assert len(all_servers) == 2, "Should have 2 configured servers"
    assert len(agent_a_servers) == 0, "Agent A has no connections"
    assert len(agent_b_servers) == 0, "Agent B has no connections"
    print(f"  [OK] All configured servers: {len(all_servers)}")
    print(f"  [OK] Agent A's connections: {len(agent_a_servers)}")
    print(f"  [OK] Agent B's connections: {len(agent_b_servers)}")

    print("\n[OK] Multi-agent scenario test PASSED!\n")


@pytest.mark.asyncio
async def test_parallel_multi_agent_multiple_servers():
    """Test: Multiple agents on multiple servers."""

    config = [
        MCPServerConfig(name="github", transport="stdio", command="npx", args=["-y"]),
        MCPServerConfig(name="filesystem", transport="stdio", command="uvx", args=[]),
    ]

    manager = MCPManager(config)

    # Mock sessions
    def create_mock_session(server_name):
        session = AsyncMock()
        tool = MagicMock(name=f"{server_name}_tool")
        tool.name = f"{server_name}_tool"
        tool.description = f"Tool from {server_name}"
        tool.inputSchema = {"type": "object"}
        session.initialize = AsyncMock()
        session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))
        return session

    # Scenario: Complex agent-server matrix
    print("\n[Complex Scenario] Building multi-agent multi-server setup...")

    with patch.object(manager, "_open_session") as mock_open:
        # Agent A connects to both servers
        mock_open.side_effect = [
            create_mock_session("github"),
            create_mock_session("filesystem"),
        ]
        await manager.connect("agent_a", "github")
        await manager.connect("agent_a", "filesystem")

    with patch.object(manager, "_open_session", new_callable=AsyncMock):
        # Agent B connects only to github (reuses A's connection)
        # No new call to _open_session for github
        await manager.connect("agent_b", "github")

    print(f"  Agent A: connected to {manager._agent_connections.get('agent_a', set())}")
    print(f"  Agent B: connected to {manager._agent_connections.get('agent_b', set())}")
    print(f"  Github count: {manager._connection_count.get('github', 0)}")
    print(f"  Filesystem count: {manager._connection_count.get('filesystem', 0)}")

    assert manager._connection_count["github"] == 2, "Github should have 2 agents"
    assert manager._connection_count["filesystem"] == 1, "Filesystem should have 1 agent"

    # Agent A disconnects from filesystem
    await manager.disconnect("agent_a", "filesystem")
    assert manager._connection_count["filesystem"] == 0, "Filesystem should be dead"
    assert manager._connection_count["github"] == 2, "Github still alive with 2 agents"

    # Agent A still connected to github
    assert manager.is_connected("agent_a", "github")

    print("  [OK] Complex scenario test PASSED!\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
