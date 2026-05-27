from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry
    from operator_use.cron.types import CronJob


# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _bold(s: str) -> str:    return f"\033[1m{s}\033[0m"
def _green(s: str) -> str:   return f"\033[1;32m{s}\033[0m"
def _yellow(s: str) -> str:  return f"\033[1;33m{s}\033[0m"
def _red(s: str) -> str:     return f"\033[1;31m{s}\033[0m"
def _grey(s: str) -> str:    return f"\033[1;30m{s}\033[0m"
def _cyan(s: str) -> str:    return f"\033[1;36m{s}\033[0m"
def _dim(s: str) -> str:     return f"\033[2m{s}\033[0m"


def _fmt_ms(ms: int | None) -> str:
    if ms is None:
        return _dim('—')
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    return dt.strftime('%Y-%m-%d %H:%M:%S %Z')


def _fmt_schedule(job: CronJob) -> str:
    s = job.schedule
    if s.mode == 'every' and s.interval_ms:
        ms = s.interval_ms
        if ms >= 86_400_000:
            human = f'{ms / 86_400_000:.4g}d'
        elif ms >= 3_600_000:
            human = f'{ms / 3_600_000:.4g}h'
        elif ms >= 60_000:
            human = f'{ms / 60_000:.4g}m'
        else:
            human = f'{ms / 1000:.4g}s'
        return f'every {human}'
    if s.mode == 'cron' and s.expr:
        tz = f' ({s.tz})' if s.tz and s.tz != 'UTC' else ''
        return f'{s.expr}{tz}'
    return repr(s)


def _fmt_status(job: CronJob) -> str:
    if not job.enabled:
        return _yellow('disabled')
    status = job.state.last_status
    if status == 'success':
        return _green('ok')
    if status == 'failure':
        return _red('failed')
    return _dim('pending')


def _print_job_list(jobs: list[CronJob]) -> None:
    col_id       = 8    # first 8 chars of id
    col_name     = max(len(j.name) for j in jobs)
    col_schedule = max(len(_fmt_schedule(j)) for j in jobs)

    header = (
        f"{'ID':<{col_id}}  "
        f"{'NAME':<{col_name}}  "
        f"{'SCHEDULE':<{col_schedule}}  "
        f"{'STATUS':<8}  "
        f"NEXT RUN"
    )
    print(_bold(header))
    print(_dim('─' * (len(header) + 16)))

    for j in jobs:
        short_id   = j.id[:col_id]
        name       = j.name[:col_name]
        schedule   = _fmt_schedule(j)
        status     = _fmt_status(j)
        next_run   = _fmt_ms(j.state.next_run_at_ms)

        print(
            f"{_cyan(short_id):<{col_id + 9}}  "
            f"{name:<{col_name}}  "
            f"{schedule:<{col_schedule}}  "
            f"{status:<{8 + 9}}  "
            f"{next_run}"
        )


def _print_job_detail(job: CronJob) -> None:
    lines = [
        ('ID',         job.id),
        ('Name',       job.name),
        ('Enabled',    _green('yes') if job.enabled else _yellow('no')),
        ('Schedule',   _fmt_schedule(job)),
        ('Message',    job.payload.message),
        ('Delete after run', _yellow('yes') if job.delete_after_run else 'no'),
        ('─────────', ''),
        ('Next run',   _fmt_ms(job.state.next_run_at_ms)),
        ('Last run',   _fmt_ms(job.state.last_run_at_ms)),
        ('Last status',
            _green('success') if job.state.last_status == 'success'
            else _red('failure') if job.state.last_status == 'failure'
            else _dim('—')),
        ('Last error',
            _red(job.state.last_error) if job.state.last_error else _dim('—')),
        ('─────────', ''),
        ('Created',    _fmt_ms(job.created_at_ms)),
        ('Updated',    _fmt_ms(job.updated_at_ms)),
    ]
    label_width = max(len(label) for label, _ in lines)
    for label, value in lines:
        if label.startswith('─'):
            print(_dim(label))
        else:
            print(f"  {_bold(label + ':'):<{label_width + 10}}  {value}")


async def _handle_cron(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    cron = runtime._context.cron
    if cron is None:
        print("Cron is disabled. Set cron_enabled=true in settings to enable it.")
        return

    jobs = cron.list_jobs()

    # /cron <job_id_prefix> — show detail for one job
    if args:
        prefix = args[0].lower()
        matches = [j for j in jobs if j.id.lower().startswith(prefix) or j.name.lower() == prefix]
        if not matches:
            print(f"No cron job found matching '{args[0]}'.")
            return
        if len(matches) > 1:
            print(f"Ambiguous: {len(matches)} jobs match '{args[0]}'. Use a longer ID prefix.")
            _print_job_list(matches)
            return
        print()
        _print_job_detail(matches[0])
        print()
        return

    # /cron — list all jobs
    if not jobs:
        print("No cron jobs scheduled. Use the cron tool to create one.")
        return

    enabled  = [j for j in jobs if j.enabled]
    disabled = [j for j in jobs if not j.enabled]

    print()
    if enabled:
        print(_bold(f"Active ({len(enabled)})"))
        _print_job_list(enabled)
    if disabled:
        if enabled:
            print()
        print(_bold(f"Disabled ({len(disabled)})"))
        _print_job_list(disabled)

    print()
    print(_dim(f"{len(jobs)} job(s) total  ·  /cron <id> for details"))
    print()


command = SlashCommandInfo(
    name='cron',
    description='List scheduled cron jobs. Pass an ID prefix or name for details.',
    handler=_handle_cron,
)
