from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from operator_use.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore
from operator_use.cron.utils import ms, job_to_dict, dict_to_job, compute_next_run

logger = logging.getLogger(__name__)


class CronJobStore:
    """
    Persists cron jobs to a JSON file and provides CRUD operations.

    The store is lazy-loaded on first access and written after every mutation.
    """

    def __init__(self, store_path: Path) -> None:
        self.store_path = Path(store_path)
        self._store: CronStore | None = None

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> CronStore:
        if self._store is not None:
            return self._store
        path = self.store_path
        if not path.exists():
            self._store = CronStore()
            return self._store
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            jobs = [dict_to_job(j) for j in data.get('jobs', [])]
            self._store = CronStore(version=data.get('version', 1), jobs=jobs)
            return self._store
        except Exception as e:
            logger.warning('Failed to load cron store from %s: %s', path, e)
            self._store = CronStore()
            return self._store

    def save(self) -> None:
        store = self.load()
        data = {
            'version': store.version,
            'jobs': [job_to_dict(j) for j in store.jobs],
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def list_jobs(self) -> list[CronJob]:
        return list(self.load().jobs)

    def get_job(self, job_id: str) -> CronJob | None:
        for j in self.load().jobs:
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
        store = self.load()
        now = ms()
        next_run = compute_next_run(schedule, now) if enabled else None
        job = CronJob(
            id=str(uuid.uuid4()),
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
        self.save()
        logger.info('Cron job added | id=%s name=%s', job.id, name)
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
        for j in self.load().jobs:
            if j.id == job_id:
                if name is not None:
                    j.name = name
                if enabled is not None:
                    j.enabled = enabled
                if schedule is not None:
                    j.schedule = schedule
                if payload is not None:
                    j.payload = payload
                now = ms()
                j.updated_at_ms = now
                j.state.next_run_at_ms = (
                    compute_next_run(j.schedule, now, j.state.last_run_at_ms)
                    if j.enabled else None
                )
                self.save()
                return j
        return None

    def remove_job(self, job_id: str) -> bool:
        store = self.load()
        for i, j in enumerate(store.jobs):
            if j.id == job_id:
                store.jobs.pop(i)
                self.save()
                logger.info('Cron job removed | id=%s', job_id)
                return True
        return False

    def enable_job(self, job_id: str) -> CronJob | None:
        return self.update_job(job_id, enabled=True)

    def disable_job(self, job_id: str) -> CronJob | None:
        return self.update_job(job_id, enabled=False)
