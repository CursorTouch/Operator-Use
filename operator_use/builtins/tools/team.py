"""Team tool — create and manage persistent multi-agent teams."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from operator_use.team.manager import TeamManager
    from operator_use.subagent.manager import SubagentManager


class TeamSchema(BaseModel):
    action: Literal['create', 'spawn', 'send', 'inbox', 'status', 'dissolve', 'list'] = Field(
        description=(
            'Action to perform:\n'
            '  create   — create a new named team.\n'
            '  spawn    — add a member to the team by spawning a subagent worker.\n'
            '  send     — send a message to a team member\'s inbox.\n'
            '  inbox    — read pending messages for a team member (clears the inbox).\n'
            '  status   — show team members and their current status.\n'
            '  dissolve — mark a team as dissolved.\n'
            '  list     — list all teams.'
        )
    )
    team_name: str | None = Field(
        default=None,
        description='Name of the team (required for all actions except list).',
    )
    description: str | None = Field(
        default=None,
        description='Team description (create action).',
    )
    member_name: str | None = Field(
        default=None,
        description='Human-readable name for the new team member (spawn action).',
    )
    role: str | None = Field(
        default=None,
        description='Subagent profile name for the spawned member (spawn action).',
    )
    task: str | None = Field(
        default=None,
        description='Task for the spawned member to execute (spawn action).',
    )
    agent_id: str | None = Field(
        default=None,
        description='Agent ID (task_id) of the target member — required for send and inbox actions.',
    )
    message: str | None = Field(
        default=None,
        description='Message text to send to the member\'s inbox (send action).',
    )

    @model_validator(mode='after')
    def _check_fields(self) -> 'TeamSchema':
        if self.action == 'list':
            return self
        if not self.team_name:
            raise ValueError(f"'team_name' is required for action='{self.action}'.")
        if self.action == 'spawn':
            missing = [f for f, v in [('member_name', self.member_name), ('role', self.role), ('task', self.task)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='spawn'.")
        elif self.action == 'send':
            missing = [f for f, v in [('agent_id', self.agent_id), ('message', self.message)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='send'.")
        elif self.action == 'inbox' and not self.agent_id:
            raise ValueError("'agent_id' is required for action='inbox'.")
        return self


def _fmt_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M')


_STATUS_ICON = {
    'active':  '🟢',
    'idle':    '🟡',
    'stopped': '⬛',
}


class TeamTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='team',
            description=(
                'Create and manage persistent multi-agent teams.\n\n'
                'Teams group subagent workers under a named team. Members persist across sessions. '
                'Use spawn to add workers, send/inbox for async messaging between agents, '
                'and status to monitor progress.'
            ),
            schema=TeamSchema,
            kind=ToolKind.Agent,
            execution_mode=ToolExecutionMode.Sequential,
        )

    def get_display_name(self, args: dict) -> str:
        """Return a human-readable description of the action."""
        action = args.get('action', '')
        team_name = args.get('team_name', '') or ''
        member_name = args.get('member_name', '') or ''
        if action == 'create': return f"Creating team: {team_name}" if team_name else "Creating team"
        if action == 'spawn': return f"Spawning member: {member_name}" if member_name else "Spawning member"
        if action == 'send': return f"Sending to: {member_name}" if member_name else "Sending to member"
        if action == 'inbox': return f"Reading inbox: {member_name}" if member_name else "Reading inbox"
        if action == 'status': return f"Team status: {team_name}" if team_name else "Team status"
        if action == 'dissolve': return f"Dissolving: {team_name}" if team_name else "Dissolving team"
        if action == 'list': return "Listing teams"
        return "Team"

    def is_available(self, context: ToolContext) -> bool:
        return context.team_manager is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Dispatch the requested action."""
        if context is None or context.team_manager is None:
            return ToolResult.error(id=invocation.id, content='TeamManager is not available.')

        team_mgr: TeamManager = context.team_manager
        sub_mgr: SubagentManager | None = context.subagent_manager
        params = invocation.params
        action = params.get('action')
        team_name = params.get('team_name') or ''

        match action:
            case 'list':
                teams = team_mgr.list_teams()
                if not teams:
                    return ToolResult.ok(id=invocation.id, content='No teams exist yet.')
                lines = [f'Teams ({len(teams)}):']
                for t in teams:
                    active = sum(1 for m in t.members if m.status == 'active')
                    lines.append(
                        f'  {"✅" if t.status == "active" else "❌"} {t.name} '
                        f'— {len(t.members)} members ({active} active)  created={_fmt_time(t.created_at)}'
                    )
                    if t.description:
                        lines.append(f'      {t.description}')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'create':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for create.")
                desc = params.get('description') or ''
                try:
                    team = team_mgr.create(team_name, desc)
                except ValueError as e:
                    return ToolResult.error(id=invocation.id, content=str(e))
                return ToolResult.ok(
                    id=invocation.id,
                    content=f"Team '{team.name}' created.  Use action='spawn' to add members.",
                )

            case 'spawn':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for spawn.")
                if team_mgr.get(team_name) is None:
                    return ToolResult.error(id=invocation.id, content=f"No team named '{team_name}'.")
                if sub_mgr is None:
                    return ToolResult.error(id=invocation.id, content='SubagentManager is not available.')

                member_name = params.get('member_name') or ''
                role = params.get('role') or ''
                task = params.get('task') or ''
                if not member_name or not role or not task:
                    return ToolResult.error(
                        id=invocation.id,
                        content="'member_name', 'role', and 'task' are all required for spawn.",
                    )

                current_depth = context.spawn_depth if context else 0
                max_depth = sub_mgr._settings.max_spawn_depth
                if current_depth >= max_depth:
                    return ToolResult.error(
                        id=invocation.id,
                        content=(
                            f'Spawn depth limit reached (depth={current_depth}, max={max_depth}). '
                            'Cannot spawn team members from this level.'
                        ),
                    )

                try:
                    task_id = await sub_mgr.invoke(
                        task,
                        label=f'{team_name}/{member_name}',
                        profile=role,
                        spawn_depth=current_depth + 1,
                        team_id=team_name,
                    )
                except ValueError as e:
                    return ToolResult.error(id=invocation.id, content=f'Cannot spawn member: {e}')

                from operator_use.team.types import TeamMember
                member = TeamMember(
                    agent_id=task_id,
                    name=member_name,
                    role=role,
                    joined_at=time.time(),
                    status='active',
                )
                team_mgr.add_member(team_name, member)

                async def _on_done(record):
                    team_mgr.update_member_status(team_name, task_id, 'stopped')

                sub_mgr.on_complete(task_id, _on_done)

                return ToolResult(
                    id=invocation.id,
                    content=(
                        f"Member '{member_name}' spawned in team '{team_name}'.\n"
                        f"  agent_id={task_id}  role={role}\n"
                        "Running in background — result will be injected automatically when done."
                    ),
                    terminate=True,
                )

            case 'send':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for send.")
                agent_id = params.get('agent_id') or ''
                message = params.get('message') or ''
                if not agent_id or not message:
                    return ToolResult.error(id=invocation.id, content="'agent_id' and 'message' are required for send.")

                if team_mgr.get(team_name) is None:
                    return ToolResult.error(id=invocation.id, content=f"No team named '{team_name}'.")

                msg_id = await team_mgr.send_message(
                    team_name, agent_id, sender_id='root', type='message', content=message
                )
                return ToolResult.ok(
                    id=invocation.id,
                    content=f"Message sent to {agent_id} in team '{team_name}' (id={msg_id}).",
                )

            case 'inbox':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for inbox.")
                agent_id = params.get('agent_id') or ''
                if not agent_id:
                    return ToolResult.error(id=invocation.id, content="'agent_id' is required for inbox.")

                if team_mgr.get(team_name) is None:
                    return ToolResult.error(id=invocation.id, content=f"No team named '{team_name}'.")

                msgs = await team_mgr.read_inbox(team_name, agent_id)
                if not msgs:
                    return ToolResult.ok(id=invocation.id, content=f"No messages in inbox for {agent_id}.")
                lines = [f"Inbox for {agent_id} in '{team_name}' ({len(msgs)} messages):"]
                for m in msgs:
                    lines.append(f"\n[{m.type}] from={m.sender_id}  at={_fmt_time(m.created_at)}")
                    lines.append(f"  {m.content}")
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'status':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for status.")
                team = team_mgr.get(team_name)
                if team is None:
                    return ToolResult.error(id=invocation.id, content=f"No team named '{team_name}'.")

                lines = [
                    f"Team: {team.name}  [{team.status}]",
                    f"  {team.description}" if team.description else '',
                    f"  created={_fmt_time(team.created_at)}  creator={team.creator_id}",
                    f"  members: {len(team.members)}",
                ]
                if team.members:
                    lines.append('')
                    for m in team.members:
                        icon = _STATUS_ICON.get(m.status, '?')
                        inbox_count = len(await team_mgr.peek_inbox(team_name, m.agent_id))
                        inbox_note = f'  📬 {inbox_count} message(s)' if inbox_count else ''
                        lines.append(
                            f"  {icon} {m.name}  agent_id={m.agent_id}  role={m.role}  "
                            f"status={m.status}{inbox_note}"
                        )
                return ToolResult.ok(id=invocation.id, content='\n'.join(l for l in lines if l != ''))

            case 'dissolve':
                if not team_name:
                    return ToolResult.error(id=invocation.id, content="'team_name' is required for dissolve.")
                try:
                    team_mgr.dissolve(team_name)
                except ValueError as e:
                    return ToolResult.error(id=invocation.id, content=str(e))
                return ToolResult.ok(id=invocation.id, content=f"Team '{team_name}' dissolved.")

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")


tool = TeamTool()
