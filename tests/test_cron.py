"""Tests for cron scheduling logic."""

import time

from operator_use.crons.views import CronSchedule, CronPayload, CronJobState, CronJob
from operator_use.crons.service import _ms, _job_to_dict, _dict_to_job, _compute_next_run, Cron


# --- _ms ---


def test_ms_returns_milliseconds():
    before = int(time.time() * 1000)
    result = _ms()
    after = int(time.time() * 1000)
    assert before <= result <= after


# --- CronSchedule / CronPayload / CronJobState ---


def test_cron_schedule_defaults():
    s = CronSchedule(mode="cron")
    assert s.interval_ms is None
    assert s.expr is None
    assert s.tz is None


def test_cron_payload_defaults():
    p = CronPayload()
    assert p.message == ""
    assert p.deliver is False
    assert p.channel is None


def test_cron_job_state_defaults():
    st = CronJobState()
    assert st.next_run_at_ms is None
    assert st.last_run_at_ms is None
    assert st.last_status is None


# --- _compute_next_run ---


def test_compute_next_run_every_mode():
    schedule = CronSchedule(mode="every", interval_ms=60000)
    now = _ms()
    next_run = _compute_next_run(schedule, from_ms=now, last_run_ms=now)
    assert next_run == now + 60000


def test_compute_next_run_every_no_last_run():
    schedule = CronSchedule(mode="every", interval_ms=5000)
    now = _ms()
    next_run = _compute_next_run(schedule, from_ms=now, last_run_ms=None)
    assert next_run == now + 5000


def test_compute_next_run_every_invalid_interval():
    schedule = CronSchedule(mode="every", interval_ms=0)
    result = _compute_next_run(schedule)
    assert result is None


def test_compute_next_run_cron_mode():
    schedule = CronSchedule(mode="cron", expr="* * * * *", tz="UTC")
    now = _ms()
    next_run = _compute_next_run(schedule, from_ms=now)
    assert next_run is not None
    assert next_run > now


def test_compute_next_run_invalid_cron():
    schedule = CronSchedule(mode="cron", expr="invalid cron expr !!!", tz="UTC")
    result = _compute_next_run(schedule)
    assert result is None


def test_compute_next_run_invalid_tz():
    schedule = CronSchedule(mode="cron", expr="* * * * *", tz="Not/ATimezone")
    result = _compute_next_run(schedule)
    assert result is None


def test_compute_next_run_unknown_mode():
    schedule = CronSchedule(mode="at")  # mode='at' uses croniter
    now = _ms()
    result = _compute_next_run(schedule, from_ms=now)
    # 'at' with no expr falls back to "* * * * *"
    assert result is not None


# --- _job_to_dict / _dict_to_job ---


def test_job_serialization_roundtrip():
    job = CronJob(
        id="job-1",
        name="daily-report",
        enabled=True,
        schedule=CronSchedule(mode="cron", expr="0 9 * * *", tz="UTC"),
        payload=CronPayload(message="run report", deliver=True, channel="telegram", chat_id="123"),
        state=CronJobState(next_run_at_ms=9999, last_run_at_ms=8888, last_status="success"),
    )
    d = _job_to_dict(job)
    restored = _dict_to_job(d)

    assert restored.id == "job-1"
    assert restored.name == "daily-report"
    assert restored.schedule.expr == "0 9 * * *"
    assert restored.payload.message == "run report"
    assert restored.state.last_status == "success"
    assert restored.state.next_run_at_ms == 9999


def test_dict_to_job_defaults():
    d = {"id": "x", "name": "test"}
    job = _dict_to_job(d)
    assert job.enabled is True
    assert job.schedule.mode == "cron"
    assert job.schedule.tz == "UTC"
    assert job.delete_after_run is False


# --- Cron service ---


def test_cron_list_jobs_empty(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    assert cron.list_jobs() == []


def test_cron_add_job(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    schedule = CronSchedule(mode="every", interval_ms=60000)
    payload = CronPayload(message="ping")
    job = cron.add_job("test-job", schedule, payload)
    assert job.name == "test-job"
    assert job.id is not None
    assert len(cron.list_jobs()) == 1


def test_cron_get_job(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job("j1", CronSchedule(mode="every", interval_ms=1000), CronPayload())
    found = cron.get_job(job.id)
    assert found is not None
    assert found.id == job.id


def test_cron_get_job_not_found(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    assert cron.get_job("nonexistent") is None


def test_cron_remove_job(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job("removable", CronSchedule(mode="every", interval_ms=1000), CronPayload())
    removed = cron.remove_job(job.id)
    assert removed is True
    assert cron.get_job(job.id) is None


def test_cron_remove_nonexistent_job(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    assert cron.remove_job("ghost") is False


def test_cron_update_job_name(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job("old-name", CronSchedule(mode="every", interval_ms=1000), CronPayload())
    updated = cron.update_job(job.id, name="new-name")
    assert updated.name == "new-name"


def test_cron_update_job_disable(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job("j", CronSchedule(mode="every", interval_ms=1000), CronPayload())
    updated = cron.update_job(job.id, enabled=False)
    assert updated.enabled is False
    assert updated.state.next_run_at_ms is None


def test_cron_persists_to_disk(tmp_path):
    path = tmp_path / "cron.json"
    cron1 = Cron(store_path=path)
    cron1.add_job(
        "persisted", CronSchedule(mode="every", interval_ms=5000), CronPayload(message="hi")
    )

    cron2 = Cron(store_path=path)
    jobs = cron2.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].name == "persisted"


def test_cron_due_jobs(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    schedule = CronSchedule(mode="every", interval_ms=1000)
    payload = CronPayload(message="due")
    job = cron.add_job("due-job", schedule, payload)
    # Force next_run_at_ms to past
    job.state.next_run_at_ms = _ms() - 5000
    cron._save()
    cron._store = None  # reset cache

    due = cron._due_jobs()
    assert any(j.id == job.id for j in due)


def test_cron_disabled_job_not_due(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job("disabled", CronSchedule(mode="every", interval_ms=1000), CronPayload())
    cron.update_job(job.id, enabled=False)
    cron._store = None
    due = cron._due_jobs()
    assert not any(j.id == job.id for j in due)


def test_cron_delete_after_run(tmp_path):
    cron = Cron(store_path=tmp_path / "cron.json")
    job = cron.add_job(
        "one-shot",
        CronSchedule(mode="every", interval_ms=1000),
        CronPayload(),
        delete_after_run=True,
    )
    assert job.delete_after_run is True
