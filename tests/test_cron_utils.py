"""Tests for cron utility functions: serialization and next-run computation."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from program.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore
from program.cron.utils import compute_next_run, dict_to_job, job_to_dict, ms
from program.cron.jobs import CronJobStore


# ── ms() ──────────────────────────────────────────────────────────────────────

class TestMs:
    def test_returns_int(self):
        result = ms()
        assert isinstance(result, int)

    def test_approximately_now(self):
        before = int(time.time() * 1000)
        result = ms()
        after = int(time.time() * 1000)
        assert before <= result <= after + 10


# ── serialisation round-trip ──────────────────────────────────────────────────

def make_job(*, mode='cron', expr='* * * * *', interval_ms=None) -> CronJob:
    return CronJob(
        id='test-id',
        name='test job',
        enabled=True,
        schedule=CronSchedule(mode=mode, expr=expr, interval_ms=interval_ms),
        payload=CronPayload(message='hello world'),
        state=CronJobState(next_run_at_ms=1000, last_run_at_ms=500, last_status='success'),
        created_at_ms=100,
        updated_at_ms=200,
        delete_after_run=False,
    )


class TestJobSerialisation:
    def test_round_trip_cron_job(self):
        job = make_job()
        d = job_to_dict(job)
        restored = dict_to_job(d)

        assert restored.id == job.id
        assert restored.name == job.name
        assert restored.enabled == job.enabled
        assert restored.schedule.mode == 'cron'
        assert restored.schedule.expr == '* * * * *'
        assert restored.payload.message == 'hello world'
        assert restored.state.last_status == 'success'
        assert restored.delete_after_run is False

    def test_round_trip_every_job(self):
        job = make_job(mode='every', interval_ms=60000)
        d = job_to_dict(job)
        restored = dict_to_job(d)

        assert restored.schedule.mode == 'every'
        assert restored.schedule.interval_ms == 60000

    def test_dict_has_required_keys(self):
        d = job_to_dict(make_job())
        for key in ('id', 'name', 'enabled', 'schedule', 'payload', 'state',
                    'created_at_ms', 'updated_at_ms', 'delete_after_run'):
            assert key in d

    def test_missing_optional_fields_use_defaults(self):
        minimal = {
            'id': 'x',
            'name': 'minimal',
            'schedule': {'mode': 'cron'},
            'payload': {},
            'state': {},
        }
        job = dict_to_job(minimal)
        assert job.enabled is True
        assert job.schedule.tz == 'UTC'
        assert job.payload.message == ''
        assert job.state.last_status is None


# ── compute_next_run ──────────────────────────────────────────────────────────

class TestComputeNextRun:
    def test_every_mode_adds_interval_to_last_run(self):
        schedule = CronSchedule(mode='every', interval_ms=5000)
        result = compute_next_run(schedule, from_ms=1000, last_run_ms=10000)
        assert result == 15000  # last_run + interval

    def test_every_mode_uses_now_when_no_last_run(self):
        now = 1_000_000
        schedule = CronSchedule(mode='every', interval_ms=3000)
        result = compute_next_run(schedule, from_ms=now, last_run_ms=None)
        assert result == now + 3000

    def test_every_mode_returns_none_for_zero_interval(self):
        schedule = CronSchedule(mode='every', interval_ms=0)
        assert compute_next_run(schedule, from_ms=1000) is None

    def test_every_mode_returns_none_for_none_interval(self):
        schedule = CronSchedule(mode='every', interval_ms=None)
        assert compute_next_run(schedule, from_ms=1000) is None

    def test_cron_mode_returns_future_timestamp(self):
        schedule = CronSchedule(mode='cron', expr='* * * * *')
        now_ms = int(time.time() * 1000)
        result = compute_next_run(schedule, from_ms=now_ms)
        assert result is not None
        assert result > now_ms

    def test_cron_mode_returns_next_minute(self):
        schedule = CronSchedule(mode='cron', expr='* * * * *')
        now_ms = int(time.time() * 1000)
        result = compute_next_run(schedule, from_ms=now_ms)
        # next run should be within 2 minutes
        assert result - now_ms <= 120_000

    def test_cron_mode_invalid_expr_returns_none(self):
        schedule = CronSchedule(mode='cron', expr='not a valid expr')
        assert compute_next_run(schedule, from_ms=1000) is None

    def test_cron_mode_invalid_tz_returns_none(self):
        schedule = CronSchedule(mode='cron', expr='* * * * *', tz='Not/A/Timezone')
        assert compute_next_run(schedule, from_ms=1000) is None

    def test_unknown_mode_returns_none(self):
        schedule = CronSchedule(mode='unknown')  # type: ignore[arg-type]
        assert compute_next_run(schedule, from_ms=1000) is None


# ── CronJobStore ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return CronJobStore(tmp_path / "cron.json")


class TestCronJobStore:
    def test_empty_store_returns_no_jobs(self, store):
        assert store.list_jobs() == []

    def test_add_and_retrieve_job(self, store):
        job = store.add_job(
            "daily digest",
            CronSchedule(mode='cron', expr='0 9 * * *'),
            CronPayload(message="Run daily report"),
        )
        assert job.name == "daily digest"
        assert store.get_job(job.id) is job  # same in-memory object (or at least same id)

    def test_add_persists_to_file(self, store):
        store.add_job("test", CronSchedule(mode='cron', expr='* * * * *'), CronPayload())
        assert store.store_path.exists()

    def test_reload_from_file(self, tmp_path):
        path = tmp_path / "cron.json"
        s1 = CronJobStore(path)
        s1.add_job("persisted", CronSchedule(mode='cron', expr='* * * * *'), CronPayload(message="hello"))
        job_id = s1.list_jobs()[0].id

        # New store instance reads from same file
        s2 = CronJobStore(path)
        reloaded = s2.get_job(job_id)
        assert reloaded is not None
        assert reloaded.name == "persisted"
        assert reloaded.payload.message == "hello"

    def test_remove_job(self, store):
        job = store.add_job("tmp", CronSchedule(mode='every', interval_ms=1000), CronPayload())
        assert store.remove_job(job.id) is True
        assert store.get_job(job.id) is None

    def test_remove_nonexistent_returns_false(self, store):
        assert store.remove_job("does-not-exist") is False

    def test_enable_disable_job(self, store):
        job = store.add_job("toggleable", CronSchedule(mode='cron', expr='* * * * *'), CronPayload())
        store.disable_job(job.id)
        assert store.get_job(job.id).enabled is False
        store.enable_job(job.id)
        assert store.get_job(job.id).enabled is True

    def test_update_name(self, store):
        job = store.add_job("old name", CronSchedule(mode='cron', expr='* * * * *'), CronPayload())
        store.update_job(job.id, name="new name")
        assert store.get_job(job.id).name == "new name"

    def test_update_nonexistent_returns_none(self, store):
        assert store.update_job("no-such-id", name="x") is None

    def test_disabled_job_has_no_next_run(self, store):
        job = store.add_job(
            "disabled",
            CronSchedule(mode='cron', expr='* * * * *'),
            CronPayload(),
            enabled=False,
        )
        assert job.state.next_run_at_ms is None

    def test_enabled_cron_job_has_next_run(self, store):
        job = store.add_job(
            "enabled",
            CronSchedule(mode='cron', expr='* * * * *'),
            CronPayload(),
        )
        assert job.state.next_run_at_ms is not None
        assert job.state.next_run_at_ms > int(time.time() * 1000)

    def test_delete_after_run_flag(self, store):
        job = store.add_job(
            "one-shot",
            CronSchedule(mode='cron', expr='* * * * *'),
            CronPayload(),
            delete_after_run=True,
        )
        assert job.delete_after_run is True
