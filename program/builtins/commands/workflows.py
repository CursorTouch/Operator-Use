from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from program.commands.types import SlashCommandInfo
from program.workflow.types import WorkflowStatus

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


def _bold(s: str) -> str:  return f'\033[1m{s}\033[0m'
def _green(s: str) -> str: return f'\033[1;32m{s}\033[0m'
def _yellow(s: str) -> str: return f'\033[1;33m{s}\033[0m'
def _red(s: str) -> str:   return f'\033[1;31m{s}\033[0m'
def _cyan(s: str) -> str:  return f'\033[1;36m{s}\033[0m'
def _dim(s: str) -> str:   return f'\033[2m{s}\033[0m'


def _fmt_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    return f'{secs // 60}m {secs % 60}s' if secs >= 60 else f'{secs}s'


def _fmt_status(status: WorkflowStatus) -> str:
    if status == WorkflowStatus.running:
        return _yellow('running')
    if status == WorkflowStatus.completed:
        return _green('done')
    if status == WorkflowStatus.failed:
        return _red('failed')
    if status == WorkflowStatus.cancelled:
        return _dim('cancelled')
    return str(status)


async def _handle_workflows(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print('No active runtime.')
        return

    manager = getattr(runtime, 'workflow_manager', None)
    if manager is None:
        print('WorkflowManager is not available.')
        return

    # /workflows discover — list available workflow files
    if args and args[0] == 'discover':
        workflows = manager.list_workflows()
        if not workflows:
            print('No workflow files found.')
            print(_dim('Place .py files in ~/.program/agent/workflows/ or <project>/.program/agent/workflows/'))
            return
        print()
        print(_bold(f'Available workflows ({len(workflows)}):'))
        for _, meta in workflows:
            print(f'  {_cyan(meta.name)} — {meta.description}')
            if meta.when_to_use:
                print(f'    {_dim("when:")} {meta.when_to_use}')
        print()
        return

    records = manager.list_all()

    # /workflows <run_id_prefix> — show detail for one run
    if args:
        prefix = args[0].lower()
        matches = [r for r in records if r.run_id.lower().startswith(prefix)]
        if not matches:
            print(f"No workflow run matching '{args[0]}'.")
            return
        r = matches[0]
        duration = _fmt_duration(r.started_at, r.finished_at)
        print()
        print(f"  {'run_id':<12} {_cyan(r.run_id)}")
        print(f"  {'name':<12} {r.workflow_name}")
        print(f"  {'status':<12} {_fmt_status(r.status)}")
        print(f"  {'duration':<12} {duration}")
        print(f"  {'started':<12} {r.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if r.finished_at:
            print(f"  {'finished':<12} {r.finished_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if r.current_phase:
            print(f"  {'phase':<12} {r.current_phase}")
        print(f"  {'agent_calls':<12} {r.agent_calls}")
        if r.log_lines:
            print()
            print(_bold(f'Log ({len(r.log_lines)} lines):'))
            for line in r.log_lines[-20:]:
                print(f'  {_dim(line)}')
        if r.result:
            print()
            print(_bold('Result:'))
            preview = r.result[:500]
            print(f'  {preview}{"..." if len(r.result) > 500 else ""}')
        if r.error:
            print()
            print(_bold('Error:'))
            print(f'  {_red(r.error)}')
        print()
        return

    # /workflows — list all runs
    if not records:
        print('No workflow runs yet. Use the workflow tool to start one.')
        return

    running   = [r for r in records if r.status == WorkflowStatus.running]
    completed = [r for r in records if r.status == WorkflowStatus.completed]
    failed    = [r for r in records if r.status in (WorkflowStatus.failed, WorkflowStatus.cancelled)]

    def _print_group(title: str, group) -> None:
        if not group:
            return
        print(_bold(f'{title} ({len(group)})'))
        for r in group:
            duration = _fmt_duration(r.started_at, r.finished_at)
            phase = f'  [{r.current_phase}]' if r.current_phase else ''
            calls = f'  {r.agent_calls} calls' if r.agent_calls else ''
            print(
                f"  {_cyan(r.run_id[:10]):<19}  "
                f"{r.workflow_name:<20}  "
                f"{_fmt_status(r.status):<14}  "
                f"{duration}{phase}{calls}"
            )

    print()
    _print_group('Running', running)
    if running and (completed or failed):
        print()
    _print_group('Completed', completed)
    if completed and failed:
        print()
    _print_group('Failed / Cancelled', failed)
    print()
    total = len(records)
    print(_dim(f'{total} run(s) total  ·  /workflows <run_id> for details  ·  /workflows discover for available'))
    print()


command = SlashCommandInfo(
    name='workflows',
    description='List workflow runs. Pass a run_id prefix for details, or "discover" for available workflows.',
    handler=_handle_workflows,
)
