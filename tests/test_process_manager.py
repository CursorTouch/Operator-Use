"""Tests for the enriched ProcessManager — shell and agent process types."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from operator_use.process.manager import ProcessManager
from operator_use.process.types import ProcessRecord, ProcessStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_tasks(tmp_path: Path) -> Path:
    d = tmp_path / 'tasks'
    d.mkdir()
    return d


@pytest.fixture
def manager(tmp_tasks: Path) -> ProcessManager:
    return ProcessManager(default_cwd=str(Path.cwd()), tasks_dir=tmp_tasks)


# ── Shell process tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shell_create_and_output(manager: ProcessManager) -> None:
    record = await manager.create(
        command='echo hello_world',
        description='echo test',
    )
    assert record.type == 'shell'
    assert record.status == ProcessStatus.RUNNING
    assert record.id.startswith('p')

    # wait for completion
    await asyncio.sleep(0.3)
    out = manager.read_output(record.id)
    assert 'hello_world' in out

    record = manager.get(record.id)
    assert record.status == ProcessStatus.COMPLETED
    assert record.return_code == 0


@pytest.mark.asyncio
async def test_shell_failed_exit_code(manager: ProcessManager) -> None:
    record = await manager.create(command='exit 1', description='fail test')
    await asyncio.sleep(0.3)
    record = manager.get(record.id)
    assert record.status == ProcessStatus.FAILED
    assert record.return_code == 1


@pytest.mark.asyncio
async def test_shell_stop(manager: ProcessManager) -> None:
    record = await manager.create(command='sleep 60', description='long sleep')
    await asyncio.sleep(0.1)
    stopped = await manager.stop(record.id)
    assert stopped.status == ProcessStatus.KILLED


@pytest.mark.asyncio
async def test_shell_list_filter(manager: ProcessManager) -> None:
    r1 = await manager.create(command='echo a', description='a')
    r2 = await manager.create(command='sleep 60', description='b')
    await asyncio.sleep(0.3)

    running = manager.list(status=ProcessStatus.RUNNING)
    assert any(r.id == r2.id for r in running)

    completed = manager.list(status=ProcessStatus.COMPLETED)
    assert any(r.id == r1.id for r in completed)

    await manager.stop(r2.id)


@pytest.mark.asyncio
async def test_shell_write_raises(manager: ProcessManager) -> None:
    record = await manager.create(command='echo x', description='x')
    with pytest.raises(ValueError, match='shell process'):
        await manager.write(record.id, 'hello')


@pytest.mark.asyncio
async def test_completion_listener_shell(manager: ProcessManager) -> None:
    completed: list[ProcessRecord] = []

    def _on_done(r: ProcessRecord) -> None:
        completed.append(r)

    manager.register_completion_listener(_on_done)
    await manager.create(command='echo done', description='listener test')
    await asyncio.sleep(0.4)
    assert len(completed) == 1
    assert completed[0].status == ProcessStatus.COMPLETED


# ── Agent process tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_record_shape(manager: ProcessManager, tmp_tasks: Path) -> None:
    """spawn_agent creates a ProcessRecord with correct shape before the session starts."""
    # We don't actually connect — just verify the record and output file are set up.
    # The session task will fail (no real operator running) but the record is created.
    record = await manager.create_agent(
        prompt='say hello',
        description='agent test',
        provider='anthropic',
        model='claude-sonnet-4-6',
    )

    assert record.type == 'agent'
    assert record.id.startswith('a')
    assert record.status == ProcessStatus.RUNNING
    assert record.prompt == 'say hello'
    assert record.command is None
    assert record.output_file is not None
    assert record.output_file.parent == tmp_tasks
    assert record.output_file.exists()

    # clean up the background session task
    await manager.stop(record.id)


@pytest.mark.asyncio
async def test_agent_output_file_written(manager: ProcessManager) -> None:
    """Output file gets the startup header written immediately."""
    record = await manager.create_agent(
        prompt='hello',
        description='output test',
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    await asyncio.sleep(0.2)

    out = manager.read_output(record.id)
    assert 'Agent started' in out
    assert 'provider=anthropic' in out

    await manager.stop(record.id)


@pytest.mark.asyncio
async def test_agent_write_before_stop(manager: ProcessManager) -> None:
    """write() enqueues a follow-up prompt without error while agent is running."""
    record = await manager.create_agent(
        prompt='first',
        description='write test',
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    # Give the session task a moment to start
    await asyncio.sleep(0.1)

    # write() should not raise — it just enqueues
    await manager.write(record.id, 'second prompt')

    await manager.stop(record.id)


@pytest.mark.asyncio
async def test_agent_write_on_stopped_raises(manager: ProcessManager) -> None:
    """write() raises ValueError if the agent process is no longer running."""
    record = await manager.create_agent(
        prompt='hi',
        description='stopped write test',
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    await manager.stop(record.id)

    with pytest.raises(ValueError, match='not running'):
        await manager.write(record.id, 'too late')


@pytest.mark.asyncio
async def test_agent_write_on_shell_raises(manager: ProcessManager) -> None:
    """write() raises ValueError for shell processes."""
    record = await manager.create(command='sleep 5', description='shell')
    with pytest.raises(ValueError, match='shell process'):
        await manager.write(record.id, 'hello')
    await manager.stop(record.id)


@pytest.mark.asyncio
async def test_completion_listener_agent(manager: ProcessManager) -> None:
    """Completion listener fires when agent process is stopped."""
    completed: list[ProcessRecord] = []

    def _on_done(r: ProcessRecord) -> None:
        completed.append(r)

    manager.register_completion_listener(_on_done)

    record = await manager.create_agent(
        prompt='hi',
        description='listener agent test',
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    await manager.stop(record.id)
    await asyncio.sleep(0.1)

    assert len(completed) >= 1
    agent_records = [r for r in completed if r.type == 'agent']
    assert len(agent_records) == 1


@pytest.mark.asyncio
async def test_unknown_process_id(manager: ProcessManager) -> None:
    with pytest.raises(KeyError):
        await manager.stop('p_does_not_exist')

    with pytest.raises(KeyError):
        manager.read_output('p_does_not_exist')
