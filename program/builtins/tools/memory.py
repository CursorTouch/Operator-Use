from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from program.memory.types import MemorySearchResult
from program.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


class MemoryAction(str, Enum):
    search = "search"
    remember = "remember"
    forget = "forget"


class MemorySchema(BaseModel):
    action: MemoryAction = Field(
        description=(
            "Memory action to perform:\n"
            "  search   — retrieve relevant long-term memories. Requires `query`.\n"
            "  remember — store a durable fact. Requires `content`.\n"
            "  forget   — remove a memory by provider id when supported. Requires `memory_id`."
        )
    )
    query: str | None = Field(default=None, description="Search query. Required for action=search.")
    content: str | None = Field(default=None, description="Durable memory content. Required for action=remember.")
    memory_id: str | None = Field(default=None, description="Provider memory id. Required for action=forget.")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum search results.")

    @model_validator(mode="after")
    def _check_action_fields(self) -> MemorySchema:
        if self.action == MemoryAction.search and not self.query:
            raise ValueError("'query' is required when action='search'")
        if self.action == MemoryAction.remember and not self.content:
            raise ValueError("'content' is required when action='remember'")
        if self.action == MemoryAction.forget and not self.memory_id:
            raise ValueError("'memory_id' is required when action='forget'")
        return self


class MemoryTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description=(
                "Provider-agnostic long-term memory tool. Use it to search existing memory, "
                "remember durable facts, forget provider-specific memory IDs when supported, "
                "Do not store raw transcripts; store stable preferences, project conventions, "
                "decisions, and facts that should survive sessions."
            ),
            schema=MemorySchema,
            kind=ToolKind.Unknown,
            execution_mode=ToolExecutionMode.Sequential,        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        manager = context.memory_manager if context else None
        if manager is None:
            return ToolResult.error(id=invocation.id, content="memory: memory manager is not available.")

        params = invocation.params
        action = params.get("action")

        try:
            match action:
                case MemoryAction.search:
                    results = manager.search(str(params.get("query", "")), limit=int(params.get("limit", 5)))
                    return ToolResult.ok(id=invocation.id, content=self._format_results(results))
                case MemoryAction.remember:
                    source = await manager.remember(str(params.get("content", "")), metadata={"source": "memory_tool"})
                    return ToolResult.ok(id=invocation.id, content=f"Memory stored: {source or 'ok'}")
                case MemoryAction.forget:
                    ok = await manager.forget(str(params.get("memory_id", "")))
                    if ok:
                        return ToolResult.ok(id=invocation.id, content="Memory forgotten.")
                    return ToolResult.error(id=invocation.id, content="memory: provider could not forget that memory.")
                case _:
                    return ToolResult.error(id=invocation.id, content=f"memory: unknown action '{action}'.")
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"memory: {exc}")

    def _format_results(self, results: list[MemorySearchResult]) -> str:
        if not results:
            return "No matching memories found."
        rows = [
            {
                "source": result.source,
                "score": result.score,
                "content": result.content,
                "metadata": result.metadata or {},
            }
            for result in results
        ]
        return json.dumps(rows, indent=2)


tool = MemoryTool()
