"""Tests for builtins/tools/plan.py — PlanTool."""
from __future__ import annotations

import pytest

from program.builtins.tools.plan import PlanTool
from program.tool.types import ToolInvocation


def _inv(params: dict) -> ToolInvocation:
    return ToolInvocation(id='i1', name='plan', params=params)


def _tool() -> PlanTool:
    return PlanTool()


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_basic(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'create', 'goal': 'Ship feature X', 'steps': ['Research', 'Implement', 'Test']}))
        assert not r.is_error
        assert 'Ship feature X' in r.content
        assert '3 steps' in r.content

    @pytest.mark.asyncio
    async def test_create_requires_goal(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'create', 'steps': ['step1']}))
        assert r.is_error
        assert 'goal' in r.content.lower()

    @pytest.mark.asyncio
    async def test_create_requires_steps(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'create', 'goal': 'Do something', 'steps': []}))
        assert r.is_error

    @pytest.mark.asyncio
    async def test_create_replaces_existing_plan(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'Old goal', 'steps': ['old step']}))
        await t.execute(_inv({'action': 'create', 'goal': 'New goal', 'steps': ['new step']}))
        r = await t.execute(_inv({'action': 'get'}))
        assert 'New goal' in r.content
        assert 'Old goal' not in r.content

    @pytest.mark.asyncio
    async def test_all_steps_start_as_pending(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['A', 'B', 'C']}))
        assert r.content.count('pending') == 3


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_status(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['step1', 'step2']}))
        r = await t.execute(_inv({'action': 'update', 'step': 1, 'status': 'done'}))
        assert not r.is_error
        assert 'done' in r.content

    @pytest.mark.asyncio
    async def test_update_with_notes(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['step1']}))
        r = await t.execute(_inv({'action': 'update', 'step': 1, 'status': 'blocked', 'notes': 'waiting on API key'}))
        assert 'waiting on API key' in r.content

    @pytest.mark.asyncio
    async def test_update_out_of_range(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['only step']}))
        r = await t.execute(_inv({'action': 'update', 'step': 5, 'status': 'done'}))
        assert r.is_error
        assert 'out of range' in r.content.lower()

    @pytest.mark.asyncio
    async def test_update_no_plan(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'update', 'step': 1, 'status': 'done'}))
        assert r.is_error

    @pytest.mark.asyncio
    async def test_update_requires_step(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['s1']}))
        r = await t.execute(_inv({'action': 'update', 'status': 'done'}))
        assert r.is_error

    @pytest.mark.asyncio
    async def test_update_requires_status(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['s1']}))
        r = await t.execute(_inv({'action': 'update', 'step': 1}))
        assert r.is_error

    @pytest.mark.asyncio
    async def test_progress_counter(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['a', 'b', 'c']}))
        await t.execute(_inv({'action': 'update', 'step': 1, 'status': 'done'}))
        r = await t.execute(_inv({'action': 'update', 'step': 2, 'status': 'done'}))
        assert '2/3 steps done' in r.content


class TestGet:
    @pytest.mark.asyncio
    async def test_get_no_plan(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'get'}))
        assert not r.is_error
        assert 'no active plan' in r.content.lower()

    @pytest.mark.asyncio
    async def test_get_shows_plan(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'My goal', 'steps': ['alpha', 'beta']}))
        r = await t.execute(_inv({'action': 'get'}))
        assert 'My goal' in r.content
        assert 'alpha' in r.content
        assert 'beta' in r.content


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_removes_plan(self):
        t = _tool()
        await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['s1']}))
        await t.execute(_inv({'action': 'clear'}))
        r = await t.execute(_inv({'action': 'get'}))
        assert 'no active plan' in r.content.lower()

    @pytest.mark.asyncio
    async def test_clear_when_no_plan(self):
        t = _tool()
        r = await t.execute(_inv({'action': 'clear'}))
        assert not r.is_error


class TestAllStatuses:
    @pytest.mark.asyncio
    async def test_all_status_values_accepted(self):
        for status in ('pending', 'in_progress', 'done', 'blocked', 'skipped'):
            t = _tool()
            await t.execute(_inv({'action': 'create', 'goal': 'G', 'steps': ['s1']}))
            r = await t.execute(_inv({'action': 'update', 'step': 1, 'status': status}))
            assert not r.is_error, f'status={status} should be valid'
