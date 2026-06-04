from __future__ import annotations

import re
from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo
from operator_use.cron.types import CronPayload, CronSchedule

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


_INTERVAL_RE = re.compile(r'^(\d+(?:\.\d+)?)(ms|s|m|h|d)$', re.IGNORECASE)
_UNITS_MS = {'ms': 1, 's': 1_000, 'm': 60_000, 'h': 3_600_000, 'd': 86_400_000}
# A cron field is either *, a number, or contains / , - (range/step/list).
_CRON_FIELD_RE = re.compile(r'^(\*|[0-9]+([,/\-][0-9*]+)*)$')


def _parse_interval(token: str) -> int | None:
    m = _INTERVAL_RE.match(token)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).lower()
    return max(1, int(value * _UNITS_MS[unit]))


def _parse_cron_expr(tokens: list[str]) -> str | None:
    """Return a 5-field cron expression if tokens[0:5] look like one, else None."""
    if len(tokens) < 5:
        return None
    fields = tokens[:5]
    if all(_CRON_FIELD_RE.match(f) for f in fields):
        return ' '.join(fields)
    return None


def _fmt_interval(ms: int) -> str:
    for unit, factor in [('d', 86_400_000), ('h', 3_600_000), ('m', 60_000), ('s', 1_000)]:
        if ms >= factor and ms % factor == 0:
            return f'{ms // factor}{unit}'
    return f'{ms}ms'


_USAGE = (
    "Usage:\n"
    "  /loop <interval> <message>              — repeat <message> every <interval>\n"
    "  /loop <MIN HR DOM MON DOW> <message>    — repeat on a cron schedule\n"
    "  /loop stop <name|id>                    — stop a running loop\n"
    "  /loop                                   — list active loops\n"
    "\n"
    "Intervals: 30s  5m  2h  1d\n"
    "Cron:      /loop 0 9 * * * daily-standup"
)


async def _handle_loop(registry: CommandRegistry, args: list[str]) -> None:
    """Execute the command with parsed arguments."""
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    cron = runtime._context.cron
    if cron is None:
        print('Cron is disabled. Set cron.enabled=true in settings to enable loops.')
        return

    # /loop — list active loops
    if not args:
        jobs = [j for j in cron.list_jobs() if j.name.startswith('loop:')]
        if not jobs:
            print("No active loops.\n" + _USAGE)
            return
        print(f"{'ID':8}  {'NAME':{max(len(j.name) for j in jobs)}}  INTERVAL  NEXT RUN")
        for j in jobs:
            interval = _fmt_interval(j.schedule.interval_ms or 0)
            from datetime import datetime, timezone
            next_run = (
                datetime.fromtimestamp(j.state.next_run_at_ms / 1000, tz=timezone.utc)
                .astimezone().strftime('%H:%M:%S')
                if j.state.next_run_at_ms else '—'
            )
            print(f"{j.id[:8]}  {j.name:{max(len(j.name) for j in jobs)}}  {interval:8}  {next_run}")
        return

    # /loop stop <name|id>
    if args[0].lower() == 'stop':
        if len(args) < 2:
            print("Usage: /loop stop <name or id prefix>")
            return
        target = args[1].lower()
        jobs = cron.list_jobs()
        matches = [
            j for j in jobs
            if j.id.lower().startswith(target) or j.name.lower() == target
        ]
        if not matches:
            print(f"No loop found matching '{args[1]}'.")
            return
        if len(matches) > 1:
            print(f"Ambiguous: {len(matches)} loops match '{args[1]}'. Use a longer ID prefix or full name.")
            for j in matches:
                print(f"  {j.id[:8]}  {j.name}")
            return
        job = matches[0]
        cron.remove_job(job.id)
        print(f"Stopped loop '{job.name}'.")
        return

    # /loop <MIN HR DOM MON DOW> <message>  — cron expression (5 fields)
    cron_expr = _parse_cron_expr(args)
    if cron_expr is not None:
        if len(args) < 6:
            print(f"No message provided after cron expression.\n{_USAGE}")
            return
        message = ' '.join(args[5:])
        schedule = CronSchedule(mode='cron', expr=cron_expr)
        schedule_label = cron_expr
    else:
        # /loop <interval> <message>
        interval_ms = _parse_interval(args[0])
        if interval_ms is None:
            print(f"Invalid interval or cron expression '{args[0]}'.\n{_USAGE}")
            return
        if len(args) < 2:
            print(f"No message provided.\n{_USAGE}")
            return
        message = ' '.join(args[1:])
        schedule = CronSchedule(mode='every', interval_ms=interval_ms)
        schedule_label = f"every {_fmt_interval(interval_ms)}"

    slug = re.sub(r'[^a-z0-9]+', '-', message[:32].lower()).strip('-')
    name = f'loop:{slug}'

    # Replace existing loop with same name rather than accumulate duplicates
    for existing in cron.list_jobs():
        if existing.name == name:
            cron.remove_job(existing.id)
            break

    job = cron.add_job(
        name=name,
        schedule=schedule,
        payload=CronPayload(message=message),
    )
    print(f"Loop '{name}' started — {schedule_label}.")
    print(f"Message: {message}")
    print(f"ID: {job.id}  ·  /loop stop {job.id[:8]} to cancel")


command = SlashCommandInfo(
    name='loop',
    description='Repeat a prompt on an interval or cron schedule. Usage: /loop <interval|cron> <message>',
    handler=_handle_loop,
)
