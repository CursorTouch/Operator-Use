import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from operator_use.workflow.types import WorkflowStatus
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from operator_use.workflow.manager import WorkflowManager


_GENERATION_SYSTEM_PROMPT = """\
You are a workflow code generator for the Operator agent system.
Generate a Python workflow file based on the user's description.

WORKFLOW API — these globals are injected at runtime, do NOT import them:
  await agent(prompt, schema=None, system=None, tools=None, resume=False)
    → str when schema is None; Pydantic model instance when schema is given
  await parallel(*thunks, concurrency=5) → list of results
    thunks are zero-argument async callables, e.g. lambda: agent(...)
  await pipeline(items, *stages, concurrency=5) → list of results
    stages are sync or async functions: item → item
  async with phase("name"): ...   — sets the current phase label in status
  log("message")                  — append a timestamped line to the run log
  budget                          — Budget object: .remaining(), .spent(), .exhausted()
  args                            — dict of key-value arguments passed at invocation

RULES:
  1. The file MUST have a top-level `meta` dict literal with at least "name" and "description".
  2. The file MUST have `async def run():` as the sole entry point.
  3. Return a string summary from run() — it becomes the workflow result shown to the user.
  4. Do NOT import the workflow globals listed above — they are already in scope.
  5. You MAY import standard library modules (json, re, datetime, pathlib, etc.).
  6. Use log() to emit progress messages so the user can track what is happening.
  7. Use phase() to label distinct stages (research, writing, review, etc.).
  8. Output ONLY the raw Python code — no markdown fences, no explanation text.
"""


async def _generate_workflow_code(llm, name: str, description: str, deliver: bool = True) -> str:
    from operator_use.inference.types import LLMContext, TextDeltaEvent, ErrorEvent
    from operator_use.message.types import UserMessage

    deliver_line = '' if deliver else '\n    "deliver": False,'
    prompt = f'Generate a workflow named "{name}" that does the following:\n\n{description}\n\nInclude in the meta dict:{deliver_line or " deliver defaults to true, no need to set it."}'
    events = await llm.invoke(LLMContext(
        messages=[UserMessage.text(prompt)],
        system_prompt=_GENERATION_SYSTEM_PROMPT,
    ))

    text = ''
    for e in events:
        if isinstance(e, TextDeltaEvent):
            text += e.text.content
        elif isinstance(e, ErrorEvent):
            raise RuntimeError(f'LLM error during code generation: {e.error}')

    # Strip markdown code fences if the model wrapped the output anyway
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        end = len(lines) - 1 if lines[-1].strip() == '```' else len(lines)
        text = '\n'.join(lines[1:end])

    return text.strip()


def _validate_workflow_code(code: str) -> str | None:
    """Return an error message if the code is invalid, None if OK."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f'Syntax error: {exc}'

    has_run = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'run'
        for node in ast.walk(tree)
    )
    if not has_run:
        return "Missing 'run' function — the workflow must define `async def run():`."

    return None


class WorkflowSchema(BaseModel):
    action: Literal['run', 'list', 'status', 'cancel', 'discover', 'create', 'delete'] = Field(
        description=(
            'Action to perform:\n'
            '  create   — generate a new workflow Python file from a description using the LLM.\n'
            '  run      — start a workflow by name (returns run_id immediately).\n'
            '             After calling run, END YOUR TURN — the result is delivered back automatically.\n'
            '  list     — show all active and recent workflow runs.\n'
            '  status   — get detailed status and result of a specific run by run_id.\n'
            '  cancel   — stop a running workflow by run_id.\n'
            '  discover — list all available workflow files with their descriptions.\n'
            '  delete   — permanently delete a workflow file by name.'
        )
    )
    name: str | None = Field(
        default=None,
        description='Workflow name (create and run actions). Used as the filename (no .py extension).',
    )
    description: str | None = Field(
        default=None,
        description=(
            'Full description of what the workflow should do (create action). '
            'Include the goal, expected inputs via args, phases, and desired output.'
        ),
    )
    args: dict | None = Field(
        default=None,
        description='Key-value arguments passed to the workflow as the `args` dict (run action).',
    )
    run_id: str | None = Field(
        default=None,
        description='Run ID — required for status and cancel actions.',
    )
    deliver: bool = Field(
        default=True,
        description='(create action) Whether to inject the result back into the main agent when done. Default true.',
    )

    @model_validator(mode='after')
    def _check_fields(self) -> 'WorkflowSchema':
        if self.action == 'create':
            missing = [f for f, v in [('name', self.name), ('description', self.description)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='create'.")
        elif self.action in {'run', 'delete'} and not self.name:
            raise ValueError(f"'name' is required for action='{self.action}'.")
        elif self.action in {'status', 'cancel'} and not self.run_id:
            raise ValueError(f"'run_id' is required for action='{self.action}'.")
        return self


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    return f'{secs // 60}m {secs % 60}s' if secs >= 60 else f'{secs}s'


_STATUS_ICON = {
    WorkflowStatus.running:   '⏳',
    WorkflowStatus.completed: '✅',
    WorkflowStatus.failed:    '❌',
    WorkflowStatus.cancelled: '🚫',
}


class WorkflowTool(Tool):
    def __init__(self, manager: WorkflowManager | None = None) -> None:
        super().__init__(
            name='workflow',
            description=(
                'Create and run Python workflow files that orchestrate multi-agent pipelines.\n\n'
                'Use action="create" to generate a new workflow from a description.\n'
                'Workflows are stored in the active profile\'s workflows/ directory.\n\n'
                'After calling run, END YOUR TURN — the result is injected back automatically '
                'when the workflow finishes.'
            ),
            schema=WorkflowSchema,
            kind=ToolKind.Workflow,
            execution_mode=ToolExecutionMode.Sequential,        )
        self._manager = manager

    def is_available(self, context) -> bool:
        sm = context.settings_manager
        if sm is not None and not sm.get_workflows_enabled():
            return False
        return (self._manager or context.workflow_manager) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        manager = self._manager or (context.workflow_manager if context else None)
        if manager is None:
            return ToolResult.error(id=invocation.id, content='WorkflowManager is not available.')

        params = invocation.params
        action = params.get('action')

        match action:
            case 'create':
                name = params.get('name')
                description = params.get('description')
                if not name:
                    return ToolResult.error(id=invocation.id, content="'name' is required for action='create'.")
                if not description:
                    return ToolResult.error(id=invocation.id, content="'description' is required for action='create'.")

                llm = context.llm if context else None
                if llm is None:
                    return ToolResult.error(id=invocation.id, content='LLM context is not available.')

                deliver = params.get('deliver', True)
                try:
                    code = await _generate_workflow_code(llm, name, description, deliver=deliver)
                except Exception as exc:
                    return ToolResult.error(id=invocation.id, content=f'Code generation failed: {exc}')

                error = _validate_workflow_code(code)
                if error:
                    return ToolResult.error(
                        id=invocation.id,
                        content=f'Generated code is invalid ({error}). Generated output:\n\n{code}',
                    )

                profile = context.resource_loader._active_profile if context and context.resource_loader else None
                path = profile.workflows_dir if profile else Path(tempfile.gettempdir()) / '.operator-workflows'
                path.mkdir(parents=True, exist_ok=True)
                workflow_path = path / f'{name}.py'
                workflow_path.write_text(code, encoding='utf-8')

                return ToolResult.ok(
                    id=invocation.id,
                    content=f"Workflow '{name}' created at {workflow_path}\n\n{code}",
                )

            case 'run':
                name = params.get('name')
                if not name:
                    return ToolResult.error(id=invocation.id, content="'name' is required for action='run'.")

                args = params.get('args') or {}
                try:
                    run_id = await manager.invoke(workflow_name=name, args=args)
                except ValueError as exc:
                    return ToolResult.error(id=invocation.id, content=f'Cannot start workflow: {exc}')

                msg = (
                    f"Workflow started — run_id={run_id}  name='{name}'\n"
                    f'Running in background — result will be injected automatically when done.'
                )
                return ToolResult(id=invocation.id, content=msg, terminate=True)

            case 'list':
                records = manager.list_all()
                if not records:
                    return ToolResult.ok(id=invocation.id, content='No workflow runs yet.')

                lines = []
                for r in records:
                    icon = _STATUS_ICON.get(r.status, '?')
                    duration = _format_duration(r.started_at, r.finished_at)
                    phase = f'  phase={r.current_phase}' if r.current_phase and r.status == WorkflowStatus.running else ''
                    line = f"{icon} {r.run_id}  [{r.status}]  {duration}  name='{r.workflow_name}'{phase}"
                    if r.status == WorkflowStatus.running:
                        line += f'  agent_calls={r.agent_calls}'
                    elif r.result:
                        preview = r.result[:120].replace('\n', ' ')
                        line += f"\n   result: {preview}{'...' if len(r.result) > 120 else ''}"
                    elif r.error:
                        line += f'\n   error: {r.error[:120]}'
                    lines.append(line)

                running = sum(1 for r in records if r.status == WorkflowStatus.running)
                header = f'Workflow runs — {len(records)} total, {running} running\n' + '─' * 60
                return ToolResult.ok(id=invocation.id, content=header + '\n' + '\n\n'.join(lines))

            case 'status':
                run_id = params.get('run_id')
                if not run_id:
                    return ToolResult.error(id=invocation.id, content="'run_id' is required for action='status'.")
                record = manager.get_record(run_id)
                if not record:
                    return ToolResult.error(id=invocation.id, content=f"No workflow run found with run_id='{run_id}'.")

                icon = _STATUS_ICON.get(record.status, '?')
                duration = _format_duration(record.started_at, record.finished_at)
                lines = [
                    f'{icon} run_id   : {record.run_id}',
                    f'   name     : {record.workflow_name}',
                    f'   status   : {record.status}',
                    f'   duration : {duration}',
                    f'   started  : {record.started_at.isoformat(timespec="seconds")}',
                ]
                if record.finished_at:
                    lines.append(f'   finished : {record.finished_at.isoformat(timespec="seconds")}')
                if record.current_phase:
                    lines.append(f'   phase    : {record.current_phase}')
                lines.append(f'   agent_calls: {record.agent_calls}')
                if record.log_lines:
                    lines.append(f'\nLog ({len(record.log_lines)} lines):')
                    lines.extend(f'  {l}' for l in record.log_lines[-20:])
                if record.result:
                    lines.append(f'\nResult:\n{record.result}')
                if record.error:
                    lines.append(f'\nError:\n{record.error}')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'cancel':
                run_id = params.get('run_id')
                if not run_id:
                    return ToolResult.error(id=invocation.id, content="'run_id' is required for action='cancel'.")
                if manager.cancel(run_id):
                    return ToolResult.ok(id=invocation.id, content=f'Workflow run {run_id} cancelled.')
                record = manager.get_record(run_id)
                if record:
                    return ToolResult.error(
                        id=invocation.id,
                        content=f"Workflow run {run_id} is not running (status={record.status}).",
                    )
                return ToolResult.error(id=invocation.id, content=f"No workflow run found with run_id='{run_id}'.")

            case 'discover':
                workflows = manager.list_workflows()
                if not workflows:
                    return ToolResult.ok(
                        id=invocation.id,
                        content=(
                            'No workflow files found.\n'
                            'Place .py files in the active profile\'s workflows/ directory, '
                            'or use action="create" to generate one.'
                        ),
                    )
                lines = [f'Available workflows ({len(workflows)}):']
                for path, meta in workflows:
                    lines.append(f'  {meta.name} — {meta.description}')
                    lines.append(f'    path: {path}')
                    if meta.when_to_use:
                        lines.append(f'    when: {meta.when_to_use}')
                return ToolResult.ok(id=invocation.id, content='\n'.join(lines))

            case 'delete':
                name = params.get('name')
                if not name:
                    return ToolResult.error(id=invocation.id, content="'name' is required for action='delete'.")
                profile = context.resource_loader._active_profile if context and context.resource_loader else None
                wf_dir = profile.workflows_dir if profile else Path(tempfile.gettempdir()) / '.operator-workflows'
                target = wf_dir / f'{name}.py'
                if not target.exists():
                    return ToolResult.error(id=invocation.id, content=f"Workflow '{name}' not found at {target}.")
                target.unlink()
                return ToolResult.ok(id=invocation.id, content=f"Workflow '{name}' deleted.")

            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")


tool = WorkflowTool()
