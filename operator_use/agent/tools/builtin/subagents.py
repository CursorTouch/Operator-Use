"""Subagents tool — spawn ephemeral anonymous workers for parallel or background tasks.

Use this tool when you need to fire off self-contained work without caring about
who does it.  Each subagent is a blank executor: no name, no workspace, no memory.
It runs its task with a fresh tool registry and disappears when done.

For delegating to a *named, persistent* peer agent that has its own workspace and
specialisation, use the ``localagents`` tool instead.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from operator_use.tools import Tool, ToolResult


class Subagents(BaseModel):
    action: Literal["create", "agents", "status", "cancel"] = Field(
        description=(
            "create — delegate a task to a new background subagent (returns task_id immediately). "
            "agents — show all subagents (running and finished) with their status and results. "
            "status — get detailed status and result of a specific subagent by task_id. "
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
        description="Subagent task_id — required for status and cancel actions.",
    )
    depends_on: Optional[list[str]] = Field(
        default=None,
        description="Optional list of task_ids that must complete before this task starts (create action). Task fails if any dependency fails.",
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
        "Spawn ephemeral anonymous workers for parallel or fire-and-forget tasks.\n\n"
        "A subagent has no identity, no workspace, and no memory — it is a blank executor "
        "that runs a task with filesystem, web, and terminal tools, then disappears. "
        "Use this when you want parallel workers for self-contained work and don't need "
        "the worker to remember anything.\n\n"
        "To delegate to a named peer agent that has its own workspace and specialisation, "
        "use the 'localagents' tool instead.\n\n"
        "Actions:\n"
        "  create — spawn a new background worker (returns task_id immediately). "
        "After calling create, END YOUR TURN — the result is delivered back automatically "
        "when done. Do not poll with 'agents'.\n"
        "  agents — show all workers and their status (only when user explicitly asks).\n"
        "  status — get detailed status and full result of a specific subagent by task_id.\n"
        "  cancel — stop a running worker by task_id."
    ),
    model=Subagents,
)
async def subagents(
    action: str,
    task: str | None = None,
    label: str | None = None,
    task_id: str | None = None,
    depends_on: list[str] | None = None,
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

            try:
                tid = await subagent_manager.ainvoke(
                    task, label, channel, chat_id, account_id, depends_on=depends_on
                )
            except ValueError as e:
                return ToolResult.error_result(f"Cannot create subagent: {e}")

            display = label or task[:60]
            msg = f"Subagent created (task_id={tid}  label='{display}')\n"
            if depends_on:
                msg += f"Dependencies: {', '.join(depends_on)}\n"
            msg += "Running in background — result will be delivered automatically when done.\nEND YOUR TURN NOW. Do not call list or any other tool. Inform the user and stop."
            return ToolResult.success_result(msg)

        case "agents":
            records = subagent_manager.list_all()
            if not records:
                return ToolResult.success_result("No subagents have been created yet.")

            lines = []
            for r in records:
                duration = _format_duration(r.started_at, r.finished_at)
                status_icon = {
                    "running": "⏳",
                    "completed": "✅",
                    "failed": "❌",
                    "cancelled": "🚫",
                }.get(r.status, "?")

                line = f"{status_icon} {r.task_id}  [{r.status}]  {duration}  label='{r.label}'"
                if r.status == "running":
                    line += f"\n   task: {r.task[:100]}"
                elif r.result:
                    preview = r.result[:120].replace("\n", " ")
                    line += f"\n   result: {preview}{'...' if len(r.result) > 120 else ''}"
                lines.append(line)

            running = sum(1 for r in records if r.status == "running")
            header = f"Subagents — {len(records)} total, {running} running\n" + "─" * 60
            return ToolResult.success_result(header + "\n" + "\n\n".join(lines))

        case "status":
            if not task_id:
                return ToolResult.error_result("Provide task_id to check status")
            record = subagent_manager.get_record(task_id)
            if not record:
                return ToolResult.error_result(f"No subagent found with task_id='{task_id}'")
            duration = _format_duration(record.started_at, record.finished_at)
            status_icon = {
                "running": "⏳",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }.get(record.status, "?")
            lines = [
                f"{status_icon} task_id : {record.task_id}",
                f"   status  : {record.status}",
                f"   label   : {record.label}",
                f"   duration: {duration}",
                f"   started : {record.started_at.isoformat(timespec='seconds')}",
            ]
            if record.finished_at:
                lines.append(f"   finished: {record.finished_at.isoformat(timespec='seconds')}")
            lines.append(f"\nTask:\n{record.task}")
            if record.result:
                lines.append(f"\nResult:\n{record.result}")
            return ToolResult.success_result("\n".join(lines))

        case "cancel":
            if not task_id:
                return ToolResult.error_result("Provide task_id to cancel")
            cancelled = subagent_manager.cancel(task_id)
            if cancelled:
                return ToolResult.success_result(f"Subagent {task_id} cancelled.")
            record = subagent_manager.get_record(task_id)
            if record:
                return ToolResult.error_result(
                    f"Subagent {task_id} is not running (status={record.status})"
                )
            return ToolResult.error_result(f"No subagent found with task_id='{task_id}'")

        case _:
            return ToolResult.error_result(f"Unknown action '{action}'")
