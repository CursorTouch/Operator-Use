from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from program.subagent.types import SubagentStatus
from program.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from program.subagent.manager import SubagentManager


class SubAgentSchema(BaseModel):
    action: Literal['create', 'list', 'status', 'cancel', 'profiles'] = Field(
        description=(
            'Action to perform:\n'
            '  create   — delegate a task to a new background subagent (returns task_id immediately).\n'
            '             After calling create, END YOUR TURN — the result is delivered back automatically.\n'
            '  list     — show all subagents (running and finished) with status and results.\n'
            '  status   — get detailed status and full result of a specific subagent by task_id.\n'
            '  cancel   — stop a running subagent by task_id.\n'
            '  profiles — list all available named subagent profiles.'
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
    profile: str = Field(
        description=(
            'Named subagent profile to use (create action). '
            'Applies the profile\'s specialized system prompt and restricts tools to its allow-list. '
            'Must match an existing profile name — use action="profiles" to see available options. '
            'Not required when fork=true.'
        ),
        default='',
    )
    fork: bool = Field(
        default=False,
        description=(
            'When true, the subagent inherits the current conversation context instead of starting '
            'fresh. It sees the full message history (compaction summary + recent messages) and '
            'uses the same system prompt as the main agent. No profile is required. '
            'Forks cannot spawn further forks. Use when the task needs full conversation context.'
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
                'Spawn named subagents for parallel or background tasks.\n\n'
                'Every subagent must use a pre-defined profile (use action="profiles" to list them). '
                'The profile determines the subagent\'s system prompt and allowed tools — '
                'anonymous subagents are not permitted.\n\n'
                'After calling create, END YOUR TURN — the result is injected back automatically '
                'when the subagent finishes. Do not poll with list or status after create.'
            ),
            schema=SubAgentSchema,
            kind=ToolKind.Agent,
            execution_mode=ToolExecutionMode.Sequential,        )
        self._manager = manager

    def is_available(self, context) -> bool:
        sm = context.settings_manager
        if sm is not None and sm.settings.subagents_enabled is False:
            return False
        return (self._manager or context.subagent_manager) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        manager = self._manager or (context.subagent_manager if context else None)
        if manager is None:
            return ToolResult.error(id=invocation.id, content='SubagentManager is not available.')

        params = invocation.params
        action = params.get('action')

        match action:
            case 'create':
                task = params.get('task')
                if not task:
                    return ToolResult.error(id=invocation.id, content="'task' is required for action='create'.")

                fork = params.get('fork', False)
                profile = params.get('profile') or ''
                label = params.get('label')
                depends_on = params.get('depends_on')

                current_depth = context.spawn_depth if context else 0
                max_depth = manager._settings.max_spawn_depth

                if fork:
                    if current_depth > 0:
                        return ToolResult.error(
                            id=invocation.id,
                            content='Forked subagents can only be spawned from the main agent (depth 0).',
                        )
                    if context is None or context.session_manager is None:
                        return ToolResult.error(
                            id=invocation.id,
                            content='Fork requires access to session context, which is unavailable.',
                        )
                    session_ctx = context.session_manager.build_session_context()
                    parent_messages = list(session_ctx.messages)
                    parent_system_prompt = context.agent.get_system_prompt() if context.agent else None
                else:
                    if not profile:
                        available = ', '.join(p.name for p in manager.list_profiles()) or 'none'
                        return ToolResult.error(
                            id=invocation.id,
                            content=f"'profile' is required for action='create'. Available profiles: {available}",
                        )
                    if current_depth >= max_depth:
                        return ToolResult.error(
                            id=invocation.id,
                            content=(
                                f'Spawn depth limit reached (depth={current_depth}, max={max_depth}). '
                                'Cannot spawn further subagents from this level.'
                            ),
                        )
                    parent_messages = None
                    parent_system_prompt = None

                try:
                    task_id = await manager.invoke(
                        task, label=label, depends_on=depends_on, profile=profile,
                        spawn_depth=current_depth + 1,
                        fork=fork,
                        parent_messages=parent_messages,
                        parent_system_prompt=parent_system_prompt,
                    )
                except ValueError as exc:
                    return ToolResult.error(id=invocation.id, content=f'Cannot create subagent: {exc}')

                display = label or task[:60]
                msg = f"Subagent triggered — task_id={task_id}  label='{display}'"
                if fork:
                    msg += '  (forked — inherits conversation context)'
                elif profile:
                    msg += f"  profile='{profile}'"
                if depends_on:
                    msg += f"\nWaiting on: {', '.join(depends_on)}"
                msg += '\nRunning in background — result will be injected automatically when done.'
                return ToolResult(id=invocation.id, content=msg, terminate=True)

            case 'list':
                records = manager.list_all()
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
                record = manager.get_record(task_id)
                if not record:
                    return ToolResult.error(id=invocation.id, content=f"No subagent found with task_id='{task_id}'.")

                icon = _STATUS_ICON.get(record.status, '?')
                duration = _format_duration(record.started_at, record.finished_at)
                lines = [
                    f'{icon} task_id : {record.task_id}',
                    f'   status  : {record.status}',
                    f'   label   : {record.label}',
                    f'   depth   : {record.spawn_depth}',
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
                if manager.cancel(task_id):
                    return ToolResult.ok(id=invocation.id, content=f'Subagent {task_id} cancelled.')
                record = manager.get_record(task_id)
                if record:
                    return ToolResult.error(
                        id=invocation.id,
                        content=f"Subagent {task_id} is not running (status={record.status}).",
                    )
                return ToolResult.error(id=invocation.id, content=f"No subagent found with task_id='{task_id}'.")

            case 'profiles':
                profiles = manager.list_profiles()
                if not profiles:
                    return ToolResult.ok(id=invocation.id, content='No subagent profiles are available.')
                lines = [f'Available subagent profiles ({len(profiles)}):']
                for p in profiles:
                    tool_note = ', '.join(p.tools) if p.tools else 'all tools'
                    lines.append(f'  {p.name} — {p.description}  [tools: {tool_note}]')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")


tool = SubagentTool()
