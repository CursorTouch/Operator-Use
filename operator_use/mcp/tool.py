"""MCP Tool — a Tool backed by a remote MCP server."""

from typing import TYPE_CHECKING
from operator_use.tools.service import Tool, ToolResult

if TYPE_CHECKING:
    from mcp import ClientSession


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
        session: "ClientSession",
    ):
        # Namespace to avoid conflicts: mcp_{server}_{tool}
        namespaced_name = f"mcp_{server_name}_{mcp_tool_name}"
        super().__init__(name=namespaced_name, description=description, model=None)
        self._mcp_tool_name = mcp_tool_name  # raw MCP tool name for call_tool()
        self._input_schema = input_schema    # MCP's inputSchema dict
        self._session = session

    @property
    def json_schema(self) -> dict:
        """Return MCP inputSchema directly — no Pydantic transformation."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._input_schema,  # already {type:object, properties:{...}, required:[...]}
        }

    async def ainvoke(self, **kwargs) -> ToolResult:
        """Call the remote MCP tool, stripping internal extension kwargs."""
        # Only include parameters that are actually defined in the MCP tool's input schema
        # This prevents unexpected parameters (like injected extensions) from reaching the MCP server
        input_schema = self._input_schema
        expected_props = set()
        if isinstance(input_schema, dict) and "properties" in input_schema:
            expected_props = set(input_schema["properties"].keys())

        # Filter to only include expected parameters
        clean_kwargs = {
            k: v for k, v in kwargs.items()
            if not k.startswith("_") and (not expected_props or k in expected_props)
        }

        # Convert non-JSON-serializable values to strings
        # This handles cases where parameters contain objects like Browser that can't be serialized
        serializable_kwargs = {}
        for k, v in clean_kwargs.items():
            try:
                # Try to JSON-serialize the value to check if it's serializable
                import json
                json.dumps(v)
                serializable_kwargs[k] = v
            except (TypeError, ValueError):
                # If not serializable, convert to string
                serializable_kwargs[k] = str(v)

        try:
            result = await self._session.call_tool(self._mcp_tool_name, serializable_kwargs)
            # result.content is List[TextContent | ImageContent | EmbeddedResource]
            parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif hasattr(item, "data"):
                    # Image or embedded resource
                    mime_type = getattr(item, "mimeType", "image")
                    parts.append(f"[image: {mime_type}]")
                else:
                    parts.append(str(item))

            output = "\n".join(parts) if parts else "(no output)"
            return ToolResult.success_result(output)
        except Exception as e:
            return ToolResult.error_result(f"MCP tool '{self._mcp_tool_name}' error: {e}")
