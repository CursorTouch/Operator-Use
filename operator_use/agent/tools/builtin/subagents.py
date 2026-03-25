"""Subagents tool — create and list background subagent tasks."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from operator_use.tools import Tool, ToolResult


class Subagents(BaseModel):
    action: Literal["create", "list", "cancel"] = Field(
        description=(
            "create — delegate a task to a new background subagent (returns task_id immediately). "
            "list — show all subagents (running and finished) with their status and results. "
            "cancel — stop a running subagent by task_id."
        )
    )
    task: Optional[str] = Field(
        default=None,
        description="Full description of the task to delegate (create action). Be specific — include all context, constraints, and expected output format.",
    )
    label: Optional[str] = Field(
        default=None,
        description="Short label for this subagent, e.g. 'research', 'file scan' (create action).",
    )
    task_id: Optional[str] = Field(
        default=None,
        description="Subagent task_id to cancel (cancel action).",
    )


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


@Tool(
    name="subagents",
    description=(
        "Manage background subagents. "
        "Use 'create' to delegate a long-running or parallelizable task to an isolated subagent — "
        "it runs independently with filesystem, web, and terminal tools. "
        "IMPORTANT: after calling 'create', do NOT poll with 'list' — the result is automatically "
        "delivered back to this conversation when the subagent finishes. Just inform the user and end your turn. "
        "Use 'list' only when the user explicitly asks to see subagent status. "
        "Use 'cancel' to stop a running subagent by task_id. "
        "Subagents cannot spawn further subagents."
    ),
    model=Subagents,
)
async def subagents(
    action: str,
    task: str | None = None,
    label: str | None = None,
    task_id: str | None = None,
    **kwargs,
) -> ToolResult:
    subagent_manager = kwargs.get("_subagent_manager")
    channel = kwargs.get("_channel")
    chat_id = kwargs.get("_chat_id")
    account_id = kwargs.get("_account_id", "")

    if not subagent_manager:
        return ToolResult.error_result("Subagent store not available (internal error)")

    match action:

        case "create":
            if not task:
                return ToolResult.error_result("Provide task description to create a subagent")
            if channel is None or chat_id is None:
                return ToolResult.error_result("Channel context not available (internal error)")
            tid = await subagent_manager.ainvoke(task, label, channel, chat_id, account_id)
            display = label or task[:60]
            return ToolResult.success_result(
                f"Subagent created (task_id={tid}  label='{display}')\n"
                f"Running in background — result will be delivered automatically when done.\n"
                f"END YOUR TURN NOW. Do not call list or any other tool. Inform the user and stop."
            )

        case "list":
            records = subagent_manager.list_all()
            if not records:
                return ToolResult.success_result("No subagents have been created yet.")

            lines = []
            for r in records:
                duration = _format_duration(r.started_at, r.finished_at)
                status_icon = {
                    "running":   "⏳",
                    "completed": "✅",
                    "failed":    "❌",
                    "cancelled": "🚫",
                }.get(r.status, "?")

                line = (
                    f"{status_icon} {r.task_id}  [{r.status}]  {duration}  label='{r.label}'"
                )
                if r.status == "running":
                    line += f"\n   task: {r.task[:100]}"
                elif r.result:
                    preview = r.result[:120].replace("\n", " ")
                    line += f"\n   result: {preview}{'...' if len(r.result) > 120 else ''}"
                lines.append(line)

            running = sum(1 for r in records if r.status == "running")
            header = f"Subagents — {len(records)} total, {running} running\n" + "─" * 60
            return ToolResult.success_result(header + "\n" + "\n\n".join(lines))

        case "cancel":
            if not task_id:
                return ToolResult.error_result("Provide task_id to cancel")
            cancelled = subagent_manager.cancel(task_id)
            if cancelled:
                return ToolResult.success_result(f"Subagent {task_id} cancelled.")
            record = subagent_manager.get_record(task_id)
            if record:
                return ToolResult.error_result(f"Subagent {task_id} is not running (status={record.status})")
            return ToolResult.error_result(f"No subagent found with task_id='{task_id}'")

        case _:
            return ToolResult.error_result(f"Unknown action '{action}'")
