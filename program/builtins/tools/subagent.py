from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from program.subagent.types import SubagentStatus
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from program.subagent.manager import SubagentManager


class SubAgentSchema(BaseModel):
    action: Literal['create', 'list', 'status', 'cancel'] = Field(
        description=(
            'Action to perform:\n'
            '  create — delegate a task to a new background subagent (returns task_id immediately).\n'
            '           After calling create, END YOUR TURN — the result is delivered back automatically.\n'
            '  list   — show all subagents (running and finished) with status and results.\n'
            '  status — get detailed status and full result of a specific subagent by task_id.\n'
            '  cancel — stop a running subagent by task_id.'
        )
    )
    task: str | None = Field(
        default=None,
        description=(
            'Full description of the task to delegate (create action). '
            'Be specific — include all context, constraints, and expected output format.'
        ),
    )
    label: str | None = Field(
        default=None,
        description="Short human-readable label for the subagent, e.g. 'research', 'file scan' (create action).",
    )
    task_id: str | None = Field(
        default=None,
        description='Subagent task_id — required for status and cancel actions.',
    )
    depends_on: list[str] | None = Field(
        default=None,
        description=(
            'Optional list of task_ids that must complete before this task starts (create action). '
            'The task is deferred until all dependencies finish.'
        ),
    )


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    return f'{secs // 60}m {secs % 60}s' if secs >= 60 else f'{secs}s'


_STATUS_ICON = {
    SubagentStatus.running:   '⏳',
    SubagentStatus.completed: '✅',
    SubagentStatus.failed:    '❌',
    SubagentStatus.cancelled: '🚫',
}


class SubagentTool(Tool):
    def __init__(self, manager: SubagentManager | None = None) -> None:
        super().__init__(
            name='subagent',
            description=(
                'Spawn ephemeral subagents for parallel or background tasks.\n\n'
                'A subagent has no identity and no memory — it is a blank executor that runs '
                'a task with the same tools as the main agent, then disappears. Use this when '
                'you want to delegate self-contained work in parallel.\n\n'
                'After calling create, END YOUR TURN — the result is injected back automatically '
                'when the subagent finishes. Do not poll with list or status after create.'
            ),
            schema=SubAgentSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._manager = manager

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
    ) -> ToolResult:
        if self._manager is None:
            return ToolResult.error(id=invocation.id, content='SubagentManager is not available.')

        params = invocation.params
        action = params.get('action')

        match action:
            case 'create':
                task = params.get('task')
                if not task:
                    return ToolResult.error(id=invocation.id, content="'task' is required for action='create'.")

                label = params.get('label')
                depends_on = params.get('depends_on')

                try:
                    task_id = await self._manager.invoke(task, label=label, depends_on=depends_on)
                except ValueError as exc:
                    return ToolResult.error(id=invocation.id, content=f'Cannot create subagent: {exc}')

                display = label or task[:60]
                msg = f"Subagent triggered — task_id={task_id}  label='{display}'"
                if depends_on:
                    msg += f"\nWaiting on: {', '.join(depends_on)}"
                msg += '\nRunning in background — result will be injected automatically when done.'
                return ToolResult(id=invocation.id, content=msg, terminate=True)

            case 'list':
                records = self._manager.list_all()
                if not records:
                    return ToolResult.ok(id=invocation.id, content='No subagents have been created yet.')

                lines = []
                for r in records:
                    icon = _STATUS_ICON.get(r.status, '?')
                    duration = _format_duration(r.started_at, r.finished_at)
                    line = f"{icon} {r.task_id}  [{r.status}]  {duration}  label='{r.label}'"
                    if r.status == SubagentStatus.running:
                        line += f'\n   task: {r.task[:100]}'
                    elif r.result:
                        preview = r.result[:120].replace('\n', ' ')
                        line += f"\n   result: {preview}{'...' if len(r.result) > 120 else ''}"
                    lines.append(line)

                running = sum(1 for r in records if r.status == SubagentStatus.running)
                header = f'Subagents — {len(records)} total, {running} running\n' + '─' * 60
                return ToolResult.ok(id=invocation.id, content=header + '\n' + '\n\n'.join(lines))

            case 'status':
                task_id = params.get('task_id')
                if not task_id:
                    return ToolResult.error(id=invocation.id, content="'task_id' is required for action='status'.")
                record = self._manager.get_record(task_id)
                if not record:
                    return ToolResult.error(id=invocation.id, content=f"No subagent found with task_id='{task_id}'.")

                icon = _STATUS_ICON.get(record.status, '?')
                duration = _format_duration(record.started_at, record.finished_at)
                lines = [
                    f'{icon} task_id : {record.task_id}',
                    f'   status  : {record.status}',
                    f'   label   : {record.label}',
                    f'   duration: {duration}',
                    f'   started : {record.started_at.isoformat(timespec="seconds")}',
                ]
                if record.finished_at:
                    lines.append(f'   finished: {record.finished_at.isoformat(timespec="seconds")}')
                if record.depends_on:
                    lines.append(f'   depends : {", ".join(record.depends_on)}')
                lines.append(f'\nTask:\n{record.task}')
                if record.result:
                    lines.append(f'\nResult:\n{record.result}')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'cancel':
                task_id = params.get('task_id')
                if not task_id:
                    return ToolResult.error(id=invocation.id, content="'task_id' is required for action='cancel'.")
                if self._manager.cancel(task_id):
                    return ToolResult.ok(id=invocation.id, content=f'Subagent {task_id} cancelled.')
                record = self._manager.get_record(task_id)
                if record:
                    return ToolResult.error(
                        id=invocation.id,
                        content=f"Subagent {task_id} is not running (status={record.status}).",
                    )
                return ToolResult.error(id=invocation.id, content=f"No subagent found with task_id='{task_id}'.")

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")


tool = SubagentTool()
