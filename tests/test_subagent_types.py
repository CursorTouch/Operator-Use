"""Tests for subagent types and TaskPool concurrency pool."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from operator_use.subagent.types import SubagentRecord, SubagentSettings, SubagentStatus
from operator_use.subagent.pool import TaskPool


# ── SubagentStatus ────────────────────────────────────────────────────────────

class TestSubagentStatus:
    def test_all_values_present(self):
        values = {s.value for s in SubagentStatus}
        assert values == {'running', 'completed', 'failed', 'cancelled'}

    def test_is_str_enum(self):
        assert str(SubagentStatus.running) == 'running'
        assert str(SubagentStatus.completed) == 'completed'


# ── SubagentRecord ────────────────────────────────────────────────────────────

class TestSubagentRecord:
    def test_create_minimal(self):
        rec = SubagentRecord(
            task_id='t1',
            label='fetch data',
            task='Fetch the data from URL X',
            status=SubagentStatus.running,
            started_at=datetime.now(),
        )
        assert rec.task_id == 't1'
        assert rec.status == SubagentStatus.running
        assert rec.result is None
        assert rec.retry_count == 0

    def test_defaults(self):
        rec = SubagentRecord(
            task_id='t2',
            label='l',
            task='t',
            status=SubagentStatus.completed,
            started_at=datetime.now(),
        )
        assert rec.depends_on == []
        assert rec.dependents == []
        assert rec.channel is None
        assert rec.chat_id is None
        assert rec.max_retries == 0

    def test_spawn_depth_default_zero(self):
        rec = SubagentRecord(
            task_id='t3', label='l', task='t',
            status=SubagentStatus.running, started_at=datetime.now(),
        )
        assert rec.spawn_depth == 0

    def test_spawn_depth_set(self):
        rec = SubagentRecord(
            task_id='t4', label='l', task='t',
            status=SubagentStatus.running, started_at=datetime.now(),
            spawn_depth=2,
        )
        assert rec.spawn_depth == 2


# ── SubagentSettings ──────────────────────────────────────────────────────────

class TestSubagentSettings:
    def test_defaults(self):
        s = SubagentSettings()
        assert s.max_concurrent == 10
        assert s.max_iterations == 20
        assert s.max_spawn_depth == 3
        assert s.timeout == 300.0
        assert s.system_prompt is None
        assert s.max_retries == 0

    def test_custom_values(self):
        s = SubagentSettings(max_concurrent=5, timeout=60.0, max_retries=3)
        assert s.max_concurrent == 5
        assert s.timeout == 60.0
        assert s.max_retries == 3

    def test_custom_spawn_depth(self):
        s = SubagentSettings(max_spawn_depth=5)
        assert s.max_spawn_depth == 5


class TestSpawnDepthEnforcement:
    @pytest.mark.asyncio
    async def test_depth_limit_blocks_spawn(self):
        from operator_use.builtins.tools.subagent import SubagentTool
        from operator_use.tool.types import ToolContext, ToolInvocation
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._settings = SubagentSettings(max_spawn_depth=2)
        manager.list_profiles.return_value = [MagicMock(name='worker')]

        tool = SubagentTool(manager=manager)
        context = ToolContext(subagent_manager=manager, spawn_depth=2)
        invocation = ToolInvocation(
            id='inv1', name='subagent',
            params={'action': 'create', 'task': 'do something', 'profile': 'worker'},
        )
        result = await tool.execute(invocation, context=context)
        assert result.is_error
        assert 'depth' in result.content.lower()

    @pytest.mark.asyncio
    async def test_depth_within_limit_proceeds(self):
        from operator_use.builtins.tools.subagent import SubagentTool
        from operator_use.tool.types import ToolContext, ToolInvocation
        from unittest.mock import AsyncMock, MagicMock

        manager = MagicMock()
        manager._settings = SubagentSettings(max_spawn_depth=3)
        manager.invoke = AsyncMock(return_value='sub_abc123')
        manager.list_profiles.return_value = [MagicMock(name='worker')]

        tool = SubagentTool(manager=manager)
        context = ToolContext(subagent_manager=manager, spawn_depth=1)
        invocation = ToolInvocation(
            id='inv2', name='subagent',
            params={'action': 'create', 'task': 'do something', 'profile': 'worker'},
        )
        result = await tool.execute(invocation, context=context)
        assert not result.is_error
        manager.invoke.assert_called_once()
        _, kwargs = manager.invoke.call_args
        assert kwargs.get('spawn_depth') == 2

    @pytest.mark.asyncio
    async def test_main_agent_depth_zero(self):
        from operator_use.tool.types import ToolContext
        ctx = ToolContext()
        assert ctx.spawn_depth == 0


# ── TaskPool ──────────────────────────────────────────────────────────────────

class TestTaskPoolBasic:
    @pytest.mark.asyncio
    async def test_single_task_runs(self):
        pool = TaskPool(max_concurrent=2)
        results = []

        async def work():
            results.append('done')

        task = pool.submit(work(), 'task1')
        await task
        await asyncio.sleep(0)
        assert 'done' in results

    @pytest.mark.asyncio
    async def test_stats_after_completion(self):
        pool = TaskPool(max_concurrent=2)
        done = []

        async def work():
            done.append(1)

        task = pool.submit(work(), 't1')
        await task
        await asyncio.sleep(0)
        stats = pool.stats()
        assert stats['max_concurrent'] == 2
        assert stats['completed'] >= 1

    @pytest.mark.asyncio
    async def test_multiple_tasks_all_complete(self):
        pool = TaskPool(max_concurrent=3)
        results = []

        async def work(i):
            results.append(i)

        tasks = [pool.submit(work(i), f't{i}') for i in range(5)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0.01)
        assert set(results) == {0, 1, 2, 3, 4}

    @pytest.mark.asyncio
    async def test_task_with_dependency_runs_after(self):
        pool = TaskPool(max_concurrent=5)
        order = []

        async def first():
            await asyncio.sleep(0.01)
            order.append('first')

        async def second():
            order.append('second')

        t1 = pool.submit(first(), 'dep')
        t2 = pool.submit(second(), 'dep_child', depends_on=['dep'])
        await asyncio.gather(t1, t2)
        await asyncio.sleep(0.05)

        # 'second' must come after 'first'
        assert order.index('first') < order.index('second')

    @pytest.mark.asyncio
    async def test_failed_task_marked_not_completed(self):
        pool = TaskPool(max_concurrent=2)

        async def bad():
            raise RuntimeError("intentional failure")

        task = pool.submit(bad(), 'fail_task')
        await task
        await asyncio.sleep(0.01)
        assert pool._completed.get('fail_task') is False

    @pytest.mark.asyncio
    async def test_unlimited_pool(self):
        pool = TaskPool(max_concurrent=0)  # 0 = unlimited
        results = []

        async def work(i):
            results.append(i)

        tasks = [pool.submit(work(i), f't{i}') for i in range(3)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0.01)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_stats_initial_state(self):
        pool = TaskPool(max_concurrent=2)
        stats = pool.stats()
        assert stats == {'max_concurrent': 2, 'pending': 0, 'running': 0, 'completed': 0}
