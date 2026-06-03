"""LCM agent tools — let the agent query the archived context store."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from operator_use.tool.types import (
    Tool, ToolInvocation, ToolResult, ToolKind,
    ToolExecutionMode, ToolExecutionUpdateCallback, AbortSignal, ToolContext,
)

if TYPE_CHECKING:
    from operator_use.compaction.strategy.lcm.dag import SummaryDAG
    from operator_use.compaction.strategy.lcm.store import MessageStore


# ---------------------------------------------------------------------------
# lcm_grep — search archived summaries and raw messages
# ---------------------------------------------------------------------------

class LCMGrepSchema(BaseModel):
    query: str = Field(description="Search query for archived conversation context.")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum results to return.")


class LCMGrepTool(Tool):
    """Search the LCM archive for compacted context by keyword."""
    def __init__(self, dag: SummaryDAG, store: MessageStore, session_id_provider) -> None:
        """Initialize with DAG and message store backends."""
        super().__init__(
            name="lcm_grep",
            description=(
                "Search archived conversation context that was compacted out of the active window. "
                "Returns matching summary nodes and raw message excerpts. "
                "Use this when you need details from earlier in the session."
            ),
            schema=LCMGrepSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )
        self._dag = dag
        self._store = store
        self._session_id_provider = session_id_provider

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Optional[ToolExecutionUpdateCallback] = None,
        signal: Optional[AbortSignal] = None,
        context: Optional[ToolContext] = None,
    ) -> ToolResult:
        """Search both DAG nodes and message store, returning formatted results."""
        params = LCMGrepSchema.model_validate(invocation.params)
        session_id = self._session_id_provider()

        dag_hits = self._dag.search(params.query, session_id, limit=params.limit)
        msg_hits = self._store.search(params.query, session_id, limit=params.limit)

        lines: list[str] = []

        if dag_hits:
            lines.append("**Summary nodes matching your query:**\n")
            for node in dag_hits:
                depth_label = f"D{node.depth}"
                lines.append(f"[node_id={node.node_id}, {depth_label}]")
                lines.append(node.summary[:800])
                if node.expand_hint:
                    lines.append(f"  → {node.expand_hint}")
                lines.append("")

        if msg_hits:
            lines.append("**Raw message excerpts:**\n")
            for msg in msg_hits:
                lines.append(f"[store_id={msg.store_id}, role={msg.role}]")
                lines.append(msg.content_text[:400])
                lines.append("")

        if not lines:
            return ToolResult.ok(invocation.id, f"No archived context found for: {params.query!r}")

        return ToolResult.ok(invocation.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# lcm_expand — drill into a specific summary node
# ---------------------------------------------------------------------------

class LCMExpandSchema(BaseModel):
    node_id: int = Field(description="node_id of the summary node to expand.")


class LCMExpandTool(Tool):
    """Drill into a specific LCM summary node to view its sources or child summaries."""
    def __init__(self, dag: SummaryDAG, store: MessageStore) -> None:
        """Initialize with DAG and message store backends."""
        super().__init__(
            name="lcm_expand",
            description=(
                "Expand a summary node from the archived context to see its source detail. "
                "For a DAG node this returns its child summaries or the raw messages it was built from."
            ),
            schema=LCMExpandSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )
        self._dag = dag
        self._store = store

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Optional[ToolExecutionUpdateCallback] = None,
        signal: Optional[AbortSignal] = None,
        context: Optional[ToolContext] = None,
    ) -> ToolResult:
        """Retrieve the specified node and its sources (children or raw messages)."""
        params = LCMExpandSchema.model_validate(invocation.params)
        node = self._dag.get_node(params.node_id)
        if not node:
            return ToolResult.error(invocation.id, f"Node {params.node_id} not found in archive.")

        lines = [f"**Node {node.node_id} (D{node.depth})**\n", node.summary, ""]

        if node.source_type == "nodes":
            children = self._dag.get_children(node)
            if children:
                lines.append(f"**Child summaries ({len(children)}):**\n")
                for child in children:
                    lines.append(f"[node_id={child.node_id}, D{child.depth}]")
                    lines.append(child.summary[:600])
                    lines.append("")
        else:
            messages = self._store.get_by_ids(node.source_ids)
            if messages:
                lines.append(f"**Source messages ({len(messages)}):**\n")
                for msg in messages:
                    lines.append(f"[{msg.role}]")
                    lines.append(msg.content_text[:600])
                    lines.append("")

        return ToolResult.ok(invocation.id, "\n".join(lines))
