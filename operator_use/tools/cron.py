"""Cron tool: manage scheduled cron jobs (add, list, remove, update)."""

from typing import Literal

from operator_use.crons.views import CronPayload, CronSchedule
from operator_use.tools import Tool, ToolResult
from pydantic import BaseModel, Field


class Cron(BaseModel):
    mode: Literal["add", "list", "remove", "update"] = Field(
        ...,
        description="Operation to perform: 'list' shows all jobs, 'add' creates a new job, 'remove' deletes by job_id, 'update' modifies an existing job by job_id.",
    )
    job_id: str | None = Field(
        default=None,
        description="The job's UUID. Required for 'remove' and 'update'. Get it from 'list'.",
    )
    name: str | None = Field(
        default=None, description="Human-readable label for the job. Required for 'add'."
    )
    schedule_mode: Literal["at", "every", "cron"] | None = Field(
        default=None,
        description="How to schedule: 'cron' or 'at' use a cron expression (schedule_expr), 'every' repeats on a fixed interval (schedule_interval_ms). Defaults to 'cron' for add.",
    )
    schedule_expr: str | None = Field(
        default=None,
        description="Standard 5-field cron expression (min hour day month weekday). Examples: '0 9 * * *' = daily 9am, '0 9 * * 1' = every Monday 9am, '*/30 * * * *' = every 30 min. Required for 'cron'/'at' modes.",
    )
    schedule_interval_ms: int | None = Field(
        default=None,
        description="Repeat interval in milliseconds for 'every' mode. Examples: 3600000 = 1 hour, 86400000 = 1 day, 300000 = 5 min. Required for 'every' mode.",
    )
    schedule_tz: str | None = Field(
        default=None,
        description="IANA timezone for cron/at scheduling (e.g. 'America/New_York', 'Asia/Kolkata'). Defaults to UTC. Ignored for 'every' mode.",
    )
    message: str | None = Field(
        default=None,
        description="Text to deliver or feed into the agent when the job fires. Required for both deliver modes: sent raw to the channel if deliver=True, or passed to the agent as a prompt if deliver=False.",
    )
    deliver: bool | None = Field(
        default=None,
        description="If True, send message directly to the channel when the job fires (bypasses the agent). If False, the job callback runs through the agent loop instead.",
    )
    channel: str | None = Field(
        default=None,
        description="Channel to deliver the message to (e.g. 'telegram', 'discord', 'slack'). Omit to use the current conversation's channel.",
    )
    chat_id: str | None = Field(
        default=None,
        description="Chat/conversation ID on the target channel. Omit to use the current conversation's chat_id.",
    )
    delete_after_run: bool = Field(
        default=False,
        description="If True, the job is automatically deleted after its first successful run. Use for one-shot reminders.",
    )
    enabled: bool | None = Field(
        default=None,
        description="Enable or disable the job without deleting it. Only used in 'update' mode.",
    )


@Tool(
    name="cron",
    description="Schedule and manage recurring or one-shot jobs. Use 'add' to create a job, 'list' to see all jobs, 'remove' to delete, 'update' to change schedule or payload. Three schedule modes: 'cron' (standard cron expression), 'every' (fixed interval in ms), 'at' (one-time via cron expr). deliver=True sends the message directly to the channel when the job fires (no agent involved). deliver=False (default) feeds the message into the agent loop so the agent can process it and respond back to the channel. Set delete_after_run=True for one-shot reminders that clean themselves up.",
    model=Cron,
)
async def cron(
    mode: Literal["add", "list", "remove", "update"],
    job_id: str | None = None,
    name: str | None = None,
    schedule_mode: Literal["at", "every", "cron"] | None = None,
    schedule_expr: str | None = None,
    schedule_interval_ms: int | None = None,
    schedule_tz: str | None = None,
    message: str | None = None,
    deliver: bool | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
    delete_after_run: bool = False,
    enabled: bool | None = None,
    **kwargs,
) -> ToolResult:
    """Single cron tool with modes: add, list, remove, update."""
    cron_svc = kwargs.get("_cron")
    if not cron_svc:
        return ToolResult.error_result("Cron service not configured (internal error)")

    if mode == "list":
        jobs = cron_svc.list_jobs()
        if not jobs:
            return ToolResult.success_result("No cron jobs found.")
        lines = []
        for j in jobs:
            next_run = j.state.next_run_at_ms
            next_str = f"{next_run}" if next_run else "none"
            s = j.schedule
            if s.mode == "every":
                sched_str = f"every {s.interval_ms}ms"
            else:
                sched_str = f"{s.mode} {s.expr or ''} tz={s.tz or 'UTC'}"
            lines.append(
                f"id={j.id} name={j.name} enabled={j.enabled} schedule={sched_str} next_run={next_str}"
            )
        return ToolResult.success_result("\n".join(lines))

    if mode == "remove":
        if not job_id:
            return ToolResult.error_result("job_id required for remove")
        if cron_svc.remove_job(job_id):
            return ToolResult.success_result(f"Removed cron job id={job_id}")
        return ToolResult.error_result(f"Job not found: {job_id}")

    if mode == "add":
        if not name:
            return ToolResult.error_result("name required for add")
        sm = schedule_mode or "cron"
        if sm == "every":
            if not schedule_interval_ms or schedule_interval_ms <= 0:
                return ToolResult.error_result("schedule_interval_ms required for mode 'every'")
            schedule = CronSchedule(mode="every", interval_ms=schedule_interval_ms)
        else:
            if not schedule_expr:
                return ToolResult.error_result("schedule_expr required for mode 'at' or 'cron'")
            schedule = CronSchedule(mode=sm, expr=schedule_expr, tz=schedule_tz or "UTC")
        ctx_channel = kwargs.get("_channel")
        ctx_chat_id = kwargs.get("_chat_id")
        ctx_account_id = kwargs.get("_account_id", "")
        payload = CronPayload(
            message=message or "",
            deliver=deliver or False,
            channel=channel if channel is not None else ctx_channel,
            chat_id=chat_id if chat_id is not None else ctx_chat_id,
            account_id=ctx_account_id,
        )
        job = cron_svc.add_job(
            name=name,
            schedule=schedule,
            payload=payload,
            delete_after_run=delete_after_run,
        )
        return ToolResult.success_result(f"Added cron job id={job.id} name={job.name}")

    if mode == "update":
        if not job_id:
            return ToolResult.error_result("job_id required for update")
        job = cron_svc.get_job(job_id)
        if not job:
            return ToolResult.error_result(f"Job not found: {job_id}")
        schedule = None
        if (
            schedule_mode is not None
            or schedule_expr is not None
            or schedule_interval_ms is not None
            or schedule_tz is not None
        ):
            s = job.schedule
            mode = schedule_mode if schedule_mode is not None else s.mode
            if mode == "every":
                schedule = CronSchedule(
                    mode="every",
                    interval_ms=schedule_interval_ms
                    if schedule_interval_ms is not None
                    else s.interval_ms,
                )
            else:
                schedule = CronSchedule(
                    mode=mode,
                    expr=schedule_expr if schedule_expr is not None else s.expr,
                    tz=schedule_tz if schedule_tz is not None else (s.tz or "UTC"),
                )
        payload = None
        if message is not None or deliver is not None or channel is not None or chat_id is not None:
            payload = CronPayload(
                message=message if message is not None else job.payload.message,
                deliver=deliver if deliver is not None else job.payload.deliver,
                channel=channel if channel is not None else job.payload.channel,
                chat_id=chat_id if chat_id is not None else job.payload.chat_id,
            )
        updated = cron_svc.update_job(
            job_id,
            name=name,
            enabled=enabled,
            schedule=schedule,
            payload=payload,
        )
        if updated:
            return ToolResult.success_result(f"Updated cron job id={job_id}")
        return ToolResult.error_result(f"Failed to update job: {job_id}")

    return ToolResult.error_result(f"Unknown mode: {mode}")
