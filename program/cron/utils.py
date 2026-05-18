from __future__ import annotations

import logging
import time

from croniter import croniter

from program.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore

logger = logging.getLogger(__name__)


def ms() -> int:
    return int(time.time() * 1000)


def job_to_dict(job: CronJob) -> dict:
    return {
        'id': job.id,
        'name': job.name,
        'enabled': job.enabled,
        'schedule': {
            'mode': job.schedule.mode,
            'interval_ms': job.schedule.interval_ms,
            'expr': job.schedule.expr,
            'tz': job.schedule.tz,
        },
        'payload': {
            'message': job.payload.message,
        },
        'state': {
            'next_run_at_ms': job.state.next_run_at_ms,
            'last_run_at_ms': job.state.last_run_at_ms,
            'last_status': job.state.last_status,
            'last_error': job.state.last_error,
        },
        'created_at_ms': job.created_at_ms,
        'updated_at_ms': job.updated_at_ms,
        'delete_after_run': job.delete_after_run,
    }


def dict_to_job(d: dict) -> CronJob:
    schedule = d.get('schedule', {})
    payload = d.get('payload', {})
    state = d.get('state', {})
    return CronJob(
        id=d['id'],
        name=d['name'],
        enabled=d.get('enabled', True),
        schedule=CronSchedule(
            mode=schedule.get('mode', 'cron'),
            interval_ms=schedule.get('interval_ms'),
            expr=schedule.get('expr'),
            tz=schedule.get('tz', 'UTC'),
        ),
        payload=CronPayload(
            message=payload.get('message', ''),
        ),
        state=CronJobState(
            next_run_at_ms=state.get('next_run_at_ms'),
            last_run_at_ms=state.get('last_run_at_ms'),
            last_status=state.get('last_status'),
            last_error=state.get('last_error'),
        ),
        created_at_ms=d.get('created_at_ms', 0),
        updated_at_ms=d.get('updated_at_ms', 0),
        delete_after_run=d.get('delete_after_run', False),
    )


def compute_next_run(
    schedule: CronSchedule,
    from_ms: int | None = None,
    last_run_ms: int | None = None,
) -> int | None:
    now = from_ms or ms()

    if schedule.mode == 'every':
        interval = schedule.interval_ms
        if not interval or interval <= 0:
            return None
        base = last_run_ms if last_run_ms is not None else now
        return base + interval

    if schedule.mode == 'cron':
        expr = schedule.expr or '* * * * *'
        tz_str = schedule.tz or 'UTC'
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_str)
            base = datetime.fromtimestamp(now / 1000, tz=tz)
            it = croniter(expr, base)
            next_dt = it.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception as e:
            logger.warning('Invalid cron expr %r or tz %r: %s', expr, tz_str, e)
            return None

    return None
