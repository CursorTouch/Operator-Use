from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from operator_use.cron.jobs import CronJobStore
from operator_use.cron.utils import ms, compute_next_run
from operator_use.cron.types import CronJob, CronPayload, CronSchedule

logger = logging.getLogger(__name__)


class CronScheduler:
    """
    Async cron scheduler. Wraps CronJobStore with a tick loop that fires jobs
    on schedule and calls on_job for each due job.

    Usage:
        scheduler = CronScheduler(store_path=path, on_job=handler)
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Awaitable[Any]] | None = None,
    ) -> None:
        self._store = CronJobStore(store_path)
        self.on_job = on_job
        self._task: asyncio.Task | None = None
        self._running = False
        # Jobs currently executing — guards against re-firing a job whose
        # handler outlasts a tick interval (next_run_at_ms isn't advanced until
        # the handler completes in _mark_run).
        self._inflight: set[str] = set()
        # Strong refs to the per-job tasks (the loop only holds weak refs).
        self._job_tasks: set[asyncio.Task] = set()

    # ── CRUD passthrough ──────────────────────────────────────────────────────

    def list_jobs(self) -> list[CronJob]:
        return self._store.list_jobs()

    def get_job(self, job_id: str) -> CronJob | None:
        return self._store.get_job(job_id)

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        payload: CronPayload,
        *,
        enabled: bool = True,
        delete_after_run: bool = False,
    ) -> CronJob:
        return self._store.add_job(
            name, schedule, payload,
            enabled=enabled,
            delete_after_run=delete_after_run,
        )

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        schedule: CronSchedule | None = None,
        payload: CronPayload | None = None,
    ) -> CronJob | None:
        return self._store.update_job(
            job_id, name=name, enabled=enabled,
            schedule=schedule, payload=payload,
        )

    def remove_job(self, job_id: str) -> bool:
        return self._store.remove_job(job_id)

    def enable_job(self, job_id: str) -> CronJob | None:
        return self._store.enable_job(job_id)

    def disable_job(self, job_id: str) -> CronJob | None:
        return self._store.disable_job(job_id)

    # ── Scheduler loop ────────────────────────────────────────────────────────

    def _due_jobs(self) -> list[CronJob]:
        now = ms()
        return [
            j for j in self._store.list_jobs()
            if j.enabled and j.state.next_run_at_ms is not None and j.state.next_run_at_ms <= now
        ]

    def _mark_run(self, job: CronJob, status: str, error: str | None = None) -> None:
        for j in self._store.load().jobs:
            if j.id == job.id:
                now = ms()
                j.state.last_run_at_ms = now
                j.state.last_status = status  # type: ignore[assignment]
                j.state.last_error = error
                j.state.next_run_at_ms = (
                    compute_next_run(j.schedule, now, now) if j.enabled else None
                )
                j.updated_at_ms = now
                self._store.save()
                break

    async def _run_job(self, job: CronJob) -> None:
        try:
            logger.info('Cron job firing | id=%s name=%s', job.id, job.name)
            if self.on_job is not None:
                await self.on_job(job)
            self._mark_run(job, 'success')
            if job.delete_after_run:
                self._store.remove_job(job.id)
        except Exception as e:
            logger.exception('Cron job failed | id=%s: %s', job.id, e)
            self._mark_run(job, 'failure', str(e))
        finally:
            # Released only after _mark_run has advanced next_run_at_ms, so the
            # next tick won't re-select this job.
            self._inflight.discard(job.id)

    async def _tick(self) -> None:
        for job in self._due_jobs():
            if job.id in self._inflight:
                continue
            self._inflight.add(job.id)
            task = asyncio.create_task(self._run_job(job))
            self._job_tasks.add(task)
            task.add_done_callback(self._job_tasks.discard)

    def _sleep_until_next(self) -> float:
        """Return seconds until the next due job, clamped between 1 and 60."""
        now_ms = ms()
        next_ms: int | None = None
        for j in self._store.list_jobs():
            if not j.enabled:
                continue
            nr = j.state.next_run_at_ms
            if nr is None:
                nr = compute_next_run(j.schedule, now_ms, j.state.last_run_at_ms)
                if nr is not None:
                    j.state.next_run_at_ms = nr
                    self._store.save()
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
        logger.info(
            'Cron scheduler started | store=%s jobs=%d',
            self._store.store_path,
            len(self._store.list_jobs()),
        )

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info('Cron scheduler stopped')


# Backward-compatible alias
Cron = CronScheduler
