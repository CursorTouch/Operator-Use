from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from croniter import croniter

from program.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore

logger = logging.getLogger(__name__)


def _ms() -> int:
    return int(time.time() * 1000)


def _job_to_dict(job: CronJob) -> dict:
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


def _dict_to_job(d: dict) -> CronJob:
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


def _compute_next_run(
    schedule: CronSchedule,
    from_ms: int | None = None,
    last_run_ms: int | None = None,
) -> int | None:
    now = from_ms or _ms()

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


class Cron:
    """Persisted cron scheduler. Creates and runs scheduled jobs, calling on_job when each fires."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Awaitable[Any]] | None = None,
    ) -> None:
        self.store_path = Path(store_path)
        self.on_job = on_job
        self._store: CronStore | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _load(self) -> CronStore:
        if self._store is not None:
            return self._store
        path = self.store_path
        if not path.exists():
            self._store = CronStore()
            return self._store
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            jobs = [_dict_to_job(j) for j in data.get('jobs', [])]
            self._store = CronStore(version=data.get('version', 1), jobs=jobs)
            return self._store
        except Exception as e:
            logger.warning('Failed to load cron store from %s: %s', path, e)
            self._store = CronStore()
            return self._store

    def _save(self) -> None:
        store = self._load()
        data = {
            'version': store.version,
            'jobs': [_job_to_dict(j) for j in store.jobs],
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    def list_jobs(self) -> list[CronJob]:
        return list(self._load().jobs)

    def get_job(self, job_id: str) -> CronJob | None:
        for j in self._load().jobs:
            if j.id == job_id:
                return j
        return None

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        payload: CronPayload,
        *,
        enabled: bool = True,
        delete_after_run: bool = False,
    ) -> CronJob:
        store = self._load()
        job_id = str(uuid.uuid4())
        now = _ms()
        next_run = _compute_next_run(schedule, now) if enabled else None
        job = CronJob(
            id=job_id,
            name=name,
            enabled=enabled,
            schedule=schedule,
            payload=payload,
            state=CronJobState(next_run_at_ms=next_run),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        store.jobs.append(job)
        self._save()
        logger.info('Cron job added | id=%s name=%s', job_id, name)
        return job

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        schedule: CronSchedule | None = None,
        payload: CronPayload | None = None,
    ) -> CronJob | None:
        store = self._load()
        for j in store.jobs:
            if j.id == job_id:
                if name is not None:
                    j.name = name
                if enabled is not None:
                    j.enabled = enabled
                if schedule is not None:
                    j.schedule = schedule
                if payload is not None:
                    j.payload = payload
                now = _ms()
                j.updated_at_ms = now
                j.state.next_run_at_ms = (
                    _compute_next_run(j.schedule, now, j.state.last_run_at_ms)
                    if j.enabled else None
                )
                self._save()
                return j
        return None

    def remove_job(self, job_id: str) -> bool:
        store = self._load()
        for i, j in enumerate(store.jobs):
            if j.id == job_id:
                store.jobs.pop(i)
                self._save()
                logger.info('Cron job removed | id=%s', job_id)
                return True
        return False

    def enable_job(self, job_id: str) -> CronJob | None:
        return self.update_job(job_id, enabled=True)

    def disable_job(self, job_id: str) -> CronJob | None:
        return self.update_job(job_id, enabled=False)

    # -------------------------------------------------------------------------
    # Scheduler loop
    # -------------------------------------------------------------------------

    def _due_jobs(self) -> list[CronJob]:
        now = _ms()
        return [
            j for j in self._load().jobs
            if j.enabled and j.state.next_run_at_ms is not None and j.state.next_run_at_ms <= now
        ]

    def _mark_run(self, job: CronJob, status: str, error: str | None = None) -> None:
        store = self._load()
        for j in store.jobs:
            if j.id == job.id:
                now = _ms()
                j.state.last_run_at_ms = now
                j.state.last_status = status  # type: ignore[assignment]
                j.state.last_error = error
                j.state.next_run_at_ms = (
                    _compute_next_run(j.schedule, now, now) if j.enabled else None
                )
                j.updated_at_ms = now
                self._save()
                break

    async def _run_job(self, job: CronJob) -> None:
        try:
            logger.info('Cron job firing | id=%s name=%s', job.id, job.name)
            if self.on_job is not None:
                await self.on_job(job)
            self._mark_run(job, 'success')
            if job.delete_after_run:
                self.remove_job(job.id)
        except Exception as e:
            logger.exception('Cron job failed | id=%s: %s', job.id, e)
            self._mark_run(job, 'failure', str(e))

    async def _tick(self) -> None:
        for job in self._due_jobs():
            asyncio.create_task(self._run_job(job))

    def _sleep_until_next(self) -> float:
        """Return seconds until the next due job, clamped between 1 and 60."""
        now_ms = _ms()
        next_ms: int | None = None
        for j in self._load().jobs:
            if not j.enabled:
                continue
            nr = j.state.next_run_at_ms
            if nr is None:
                nr = _compute_next_run(j.schedule, now_ms, j.state.last_run_at_ms)
                if nr is not None:
                    j.state.next_run_at_ms = nr
                    self._save()
            if nr is not None and (next_ms is None or nr < next_ms):
                next_ms = nr
        if next_ms is None:
            return 60.0
        delay = (next_ms - now_ms) / 1000.0
        return max(1.0, min(60.0, delay))

    async def _loop(self) -> None:
        while self._running:
            try:
                delay = self._sleep_until_next()
                await asyncio.sleep(delay)
                if not self._running:
                    break
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error('Error in cron loop: %s', e)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info('Cron service started | store=%s jobs=%d', self.store_path, len(self._load().jobs))

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info('Cron service stopped')
