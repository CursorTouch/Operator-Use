"""MCPTool — a Tool backed by a remote MCP server."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional, Type

from pydantic import BaseModel

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from fastmcp import Client


class MCPTool(Tool):
    """Wraps one tool exposed by a remote MCP server.

    The MCP server's JSON schema is passed through to the LLM as-is.
    Tool names are namespaced: mcp_{server_name}_{tool_name}.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict,
        client: Client,
    ) -> None:
        super().__init__(
            name=f'mcp_{server_name}_{tool_name}',
            description=description,
            schema=_EmptyModel,        # placeholder; overridden below
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._tool_name = tool_name    # original name on the server
        self._input_schema = input_schema
        self._client = client

    # ── Override schema handling ──────────────────────────────────────────────

    def validate(self, params: dict[str, Any]) -> tuple[bool, list[str]]:
        # Validation is handled by the MCP server; accept everything here.
        return True, []

    def to_json(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self._input_schema,
        }

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal: Optional[Any] = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        # Strip internal kwargs (keys starting with '_') before forwarding.
        schema_props: set[str] = set()
        if isinstance(self._input_schema, dict) and 'properties' in self._input_schema:
            schema_props = set(self._input_schema['properties'].keys())

        params = {
            k: v for k, v in invocation.params.items()
            if not k.startswith('_') and (not schema_props or k in schema_props)
        }

        # Ensure all values are JSON-serializable.
        clean: dict[str, Any] = {}
        for k, v in params.items():
            try:
                json.dumps(v)
                clean[k] = v
            except (TypeError, ValueError):
                clean[k] = str(v)

        try:
            items = await self._client.call_tool(self._tool_name, clean)
            parts = []
            for item in items:
                if hasattr(item, 'text'):
                    parts.append(item.text)
                elif hasattr(item, 'data'):
                    mime = getattr(item, 'mimeType', 'image')
                    parts.append(f'[image: {mime}]')
                else:
                    parts.append(str(item))
            output = '\n'.join(parts) if parts else '(no output)'
            return ToolResult.ok(id=invocation.id, content=output)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"MCP tool {self._tool_name!r} error: {exc}")


class _EmptyModel(BaseModel):
    """Placeholder Pydantic model — MCPTool overrides validate() and to_json()."""
    pass
