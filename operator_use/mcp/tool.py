"""MCP Tool — a Tool backed by a remote MCP server via fastmcp."""

import json
from typing import TYPE_CHECKING

from operator_use.agent.tools.service import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp import Client


class MCPTool(Tool):
    """A Tool whose implementation is a remote MCP server tool.

    The MCP tool's JSON schema (inputSchema) is passed through as-is
    to the LLM — no Pydantic coercion.
    """

    def __init__(
        self,
        server_name: str,
        mcp_tool_name: str,
        description: str,
        input_schema: dict,
        client: "Client",
    ):
        namespaced_name = f"mcp_{server_name}_{mcp_tool_name}"
        super().__init__(name=namespaced_name, description=description, model=None)
        self._mcp_tool_name = mcp_tool_name
        self._input_schema = input_schema
        self._client = client

    @property
    def json_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._input_schema,
        }

    async def ainvoke(self, **kwargs) -> ToolResult:
        """Call the remote MCP tool via fastmcp client."""
        expected_props: set[str] = set()
        if isinstance(self._input_schema, dict) and "properties" in self._input_schema:
            expected_props = set(self._input_schema["properties"].keys())

        clean_kwargs = {
            k: v
            for k, v in kwargs.items()
            if not k.startswith("_") and (not expected_props or k in expected_props)
        }

        serializable_kwargs = {}
        for k, v in clean_kwargs.items():
            try:
                json.dumps(v)
                serializable_kwargs[k] = v
            except (TypeError, ValueError):
                serializable_kwargs[k] = str(v)

        try:
            # fastmcp Client.call_tool() returns list[Content] directly
            content_items = await self._client.call_tool(self._mcp_tool_name, serializable_kwargs)
            parts = []
            for item in content_items:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif hasattr(item, "data"):
                    mime_type = getattr(item, "mimeType", "image")
                    parts.append(f"[image: {mime_type}]")
                else:
                    parts.append(str(item))

            output = "\n".join(parts) if parts else "(no output)"
            return ToolResult.success_result(output)
        except Exception as e:
            return ToolResult.error_result(f"MCP tool '{self._mcp_tool_name}' error: {e}")
