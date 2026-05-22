import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from program.cron.scheduler import Cron
    from program.cron.types import CronJob


class CronSchema(BaseModel):
    action: Literal['list', 'add', 'update', 'remove', 'enable', 'disable'] = Field(
        description=(
            'Action to perform:\n'
            '  list    — list all cron jobs\n'
            '  add     — create a new scheduled job\n'
            '  update  — modify an existing job\n'
            '  remove  — delete a job\n'
            '  enable  — re-enable a disabled job\n'
            '  disable — pause a job without deleting it'
        )
    )
    job_id: str | None = Field(
        default=None,
        description='Job ID (required for update, remove, enable, disable).',
    )
    name: str | None = Field(
        default=None,
        description='Human-readable label for the job.',
    )
    schedule_mode: Literal['every', 'cron'] | None = Field(
        default=None,
        description=(
            'Scheduling mode:\n'
            '  every — repeat on a fixed interval (requires interval_ms)\n'
            '  cron  — standard cron expression, e.g. "0 9 * * 1-5" (requires expr)'
        ),
    )
    interval_ms: int | None = Field(
        default=None,
        description='Interval in milliseconds. Used when schedule_mode="every". E.g. 3600000 = 1 hour.',
    )
    expr: str | None = Field(
        default=None,
        description='Cron expression (5 fields: minute hour dom month dow). Used when schedule_mode="cron".',
    )
    tz: str | None = Field(
        default=None,
        description='IANA timezone for cron expressions, e.g. "America/New_York". Defaults to "UTC".',
    )
    message: str | None = Field(
        default=None,
        description='Prompt that will be sent to the agent when the job fires.',
    )
    delete_after_run: bool = Field(
        default=False,
        description='If true, the job is automatically removed after its first successful run.',
    )
    channel_id: str | None = Field(
        default=None,
        description=(
            'Channel to send the response to when the job fires '
            '(e.g. "telegram", "discord", "slack"). '
            'If omitted the response goes to the active agent session.'
        ),
    )
    chat_id: str | None = Field(
        default=None,
        description='Specific chat or user ID within the channel (required when channel_id is set).',
    )
    deliver: bool | None = Field(
        default=None,
        description=(
            'If True, send message directly to the channel when the job fires (bypasses the agent). '
            'If False (default), the job callback runs through the agent loop instead.'
        ),
    )


def _format_job(job: CronJob) -> dict:
    sched = job.schedule
    if sched.mode == 'every' and sched.interval_ms:
        schedule_str = f'every {sched.interval_ms}ms'
    elif sched.mode == 'cron' and sched.expr:
        schedule_str = f'cron({sched.expr}) tz={sched.tz}'
    else:
        schedule_str = repr(sched)

    return {
        'id': job.id,
        'name': job.name,
        'enabled': job.enabled,
        'schedule': schedule_str,
        'message': job.payload.message,
        'deliver': job.payload.deliver,
        'delete_after_run': job.delete_after_run,
        'next_run_at_ms': job.state.next_run_at_ms,
        'last_status': job.state.last_status,
        'last_error': job.state.last_error,
    }


class CronTool(Tool):
    def __init__(self, cron: Cron | None = None) -> None:
        super().__init__(
            name='cron',
            description=(
                'Manage scheduled cron jobs. Jobs fire on a fixed interval or cron expression '
                'and inject a prompt into the agent. Use to schedule reminders, recurring tasks, '
                'or timed automations.'
            ),
            schema=CronSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._cron = cron

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
    ) -> ToolResult:
        if self._cron is None:
            return ToolResult.error(id=invocation.id, content='Cron service is not available.')

        from program.cron.types import CronPayload, CronSchedule

        cron = self._cron
        params = invocation.params
        action = params.get('action')

        try:
            if action == 'list':
                jobs = cron.list_jobs()
                if not jobs:
                    return ToolResult.ok(id=invocation.id, content='No cron jobs scheduled.')
                return ToolResult.ok(
                    id=invocation.id,
                    content=json.dumps([_format_job(j) for j in jobs], indent=2),
                )

            elif action == 'add':
                name = params.get('name')
                schedule_mode = params.get('schedule_mode')
                message = params.get('message')

                if not name:
                    return ToolResult.error(id=invocation.id, content="'name' is required for action='add'.")
                if not schedule_mode:
                    return ToolResult.error(id=invocation.id, content="'schedule_mode' is required for action='add'.")
                if not message:
                    return ToolResult.error(id=invocation.id, content="'message' is required for action='add'.")

                if schedule_mode == 'every':
                    interval_ms = params.get('interval_ms')
                    if not interval_ms or interval_ms <= 0:
                        return ToolResult.error(
                            id=invocation.id,
                            content="'interval_ms' must be a positive integer when schedule_mode='every'.",
                        )
                    schedule = CronSchedule(mode='every', interval_ms=interval_ms)
                else:
                    expr = params.get('expr')
                    if not expr:
                        return ToolResult.error(
                            id=invocation.id,
                            content="'expr' is required when schedule_mode='cron'.",
                        )
                    schedule = CronSchedule(mode='cron', expr=expr, tz=params.get('tz') or 'UTC')

                job = cron.add_job(
                    name=name,
                    schedule=schedule,
                    payload=CronPayload(
                        message=message,
                        channel_id=params.get('channel_id'),
                        chat_id=params.get('chat_id'),
                        deliver=params.get('deliver') or False,
                    ),
                    delete_after_run=params.get('delete_after_run', False),
                )
                return ToolResult.ok(
                    id=invocation.id,
                    content=f"Cron job created.\n{json.dumps(_format_job(job), indent=2)}",
                )

            elif action == 'update':
                job_id = params.get('job_id')
                if not job_id:
                    return ToolResult.error(id=invocation.id, content="'job_id' is required for action='update'.")

                schedule = None
                schedule_mode = params.get('schedule_mode')
                if schedule_mode == 'every':
                    interval_ms = params.get('interval_ms')
                    if not interval_ms:
                        return ToolResult.error(
                            id=invocation.id,
                            content="'interval_ms' is required when updating schedule_mode='every'.",
                        )
                    schedule = CronSchedule(mode='every', interval_ms=interval_ms)
                elif schedule_mode == 'cron':
                    expr = params.get('expr')
                    if not expr:
                        return ToolResult.error(
                            id=invocation.id,
                            content="'expr' is required when updating schedule_mode='cron'.",
                        )
                    schedule = CronSchedule(mode='cron', expr=expr, tz=params.get('tz') or 'UTC')

                message = params.get('message')
                deliver = params.get('deliver')
                if message or deliver is not None:
                    payload = CronPayload(
                        message=message or '',
                        channel_id=params.get('channel_id'),
                        chat_id=params.get('chat_id'),
                        deliver=deliver or False,
                    )
                else:
                    payload = None

                updated = cron.update_job(
                    job_id,
                    name=params.get('name'),
                    schedule=schedule,
                    payload=payload,
                )
                if updated is None:
                    return ToolResult.error(id=invocation.id, content=f"No job found with id='{job_id}'.")
                return ToolResult.ok(
                    id=invocation.id,
                    content=f"Cron job updated.\n{json.dumps(_format_job(updated), indent=2)}",
                )

            elif action == 'remove':
                job_id = params.get('job_id')
                if not job_id:
                    return ToolResult.error(id=invocation.id, content="'job_id' is required for action='remove'.")
                if not cron.remove_job(job_id):
                    return ToolResult.error(id=invocation.id, content=f"No job found with id='{job_id}'.")
                return ToolResult.ok(id=invocation.id, content=f"Cron job '{job_id}' removed.")

            elif action == 'enable':
                job_id = params.get('job_id')
                if not job_id:
                    return ToolResult.error(id=invocation.id, content="'job_id' is required for action='enable'.")
                if cron.enable_job(job_id) is None:
                    return ToolResult.error(id=invocation.id, content=f"No job found with id='{job_id}'.")
                return ToolResult.ok(id=invocation.id, content=f"Cron job '{job_id}' enabled.")

            elif action == 'disable':
                job_id = params.get('job_id')
                if not job_id:
                    return ToolResult.error(id=invocation.id, content="'job_id' is required for action='disable'.")
                if cron.disable_job(job_id) is None:
                    return ToolResult.error(id=invocation.id, content=f"No job found with id='{job_id}'.")
                return ToolResult.ok(id=invocation.id, content=f"Cron job '{job_id}' disabled.")

            else:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")

        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Cron operation failed: {e}")


tool = CronTool()
