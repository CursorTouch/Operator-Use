from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from program.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


class PlanSchema(BaseModel):
    action: Literal['create', 'update', 'get', 'clear'] = Field(
        description=(
            'Plan action to perform:\n'
            '  create — start a new plan with a goal and ordered steps. Replaces any existing plan.\n'
            '  update — change the status (and optionally add notes) for a specific step.\n'
            '  get    — display the current plan and all step statuses.\n'
            '  clear  — discard the current plan entirely.'
        )
    )
    goal: str | None = Field(
        default=None,
        description='High-level goal the plan is working toward. Required for action=create.',
    )
    steps: list[str] | None = Field(
        default=None,
        description='Ordered list of step descriptions. Required for action=create.',
    )
    step: int | None = Field(
        default=None,
        description='1-based step number to update. Required for action=update.',
    )
    status: Literal['pending', 'in_progress', 'done', 'blocked', 'skipped'] | None = Field(
        default=None,
        description='New status for the step. Required for action=update.',
    )
    notes: str | None = Field(
        default=None,
        description='Optional notes to attach to the step (action=update).',
    )


@dataclass
class _Step:
    description: str
    status: str = 'pending'
    notes: str = ''


@dataclass
class _Plan:
    goal: str
    steps: list[_Step] = field(default_factory=list)


_STATUS_ICON = {
    'pending':     '○',
    'in_progress': '◉',
    'done':        '✓',
    'blocked':     '✗',
    'skipped':     '—',
}


def _render(plan: _Plan) -> str:
    lines = [f'Goal: {plan.goal}', '']
    for i, s in enumerate(plan.steps, 1):
        icon = _STATUS_ICON.get(s.status, '?')
        line = f'  {i}. [{icon}] {s.description}  ({s.status})'
        if s.notes:
            line += f'\n       ↳ {s.notes}'
        lines.append(line)
    done = sum(1 for s in plan.steps if s.status == 'done')
    lines.append(f'\n{done}/{len(plan.steps)} steps done')
    return '\n'.join(lines)


class PlanTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='plan',
            description=(
                'Create and track a structured plan for the current task.\n\n'
                'Use create at the start of any multi-step task to lay out the steps. '
                'Use update to tick off steps as you complete them. '
                'Use get to review where you are. '
                'One active plan per session — create replaces the previous one.'
            ),
            schema=PlanSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._plan: _Plan | None = None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        action = params.get('action')

        match action:
            case 'create':
                goal = params.get('goal')
                steps = params.get('steps')
                if not goal:
                    return ToolResult.error(id=invocation.id, content="'goal' is required for action=create.")
                if not steps or not isinstance(steps, list) or len(steps) == 0:
                    return ToolResult.error(id=invocation.id, content="'steps' must be a non-empty list for action=create.")
                self._plan = _Plan(goal=goal, steps=[_Step(description=s) for s in steps])
                return ToolResult.ok(id=invocation.id, content=f'Plan created with {len(steps)} steps.\n\n' + _render(self._plan))

            case 'update':
                if self._plan is None:
                    return ToolResult.error(id=invocation.id, content='No active plan. Use action=create first.')
                step_num = params.get('step')
                status = params.get('status')
                if step_num is None:
                    return ToolResult.error(id=invocation.id, content="'step' is required for action=update.")
                if status is None:
                    return ToolResult.error(id=invocation.id, content="'status' is required for action=update.")
                if not isinstance(step_num, int) or step_num < 1 or step_num > len(self._plan.steps):
                    return ToolResult.error(
                        id=invocation.id,
                        content=f'Step {step_num} is out of range (plan has {len(self._plan.steps)} steps).',
                    )
                step = self._plan.steps[step_num - 1]
                step.status = status
                if params.get('notes'):
                    step.notes = params['notes']
                return ToolResult.ok(id=invocation.id, content=_render(self._plan))

            case 'get':
                if self._plan is None:
                    return ToolResult.ok(id=invocation.id, content='No active plan.')
                return ToolResult.ok(id=invocation.id, content=_render(self._plan))

            case 'clear':
                self._plan = None
                return ToolResult.ok(id=invocation.id, content='Plan cleared.')

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")


tool = PlanTool()
