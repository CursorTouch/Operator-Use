"""Tests for builtins/tools/todo.py -- TodoTool."""
from __future__ import annotations

import json

import pytest

from operator_use.builtins.tools.todo import TodoTool
from operator_use.message.types import AssistantMessage, ToolCallContent, ToolMessage, ToolResultContent
from operator_use.tool.types import ToolInvocation


def _inv(params: dict) -> ToolInvocation:
    return ToolInvocation(id='i1', name='todo', params=params)


def _tool() -> TodoTool:
    return TodoTool()


def _json(content: str) -> dict:
    return json.loads(content)


class TestReadWrite:
    @pytest.mark.asyncio
    async def test_read_empty_list(self):
        t = _tool()

        r = await t.execute(_inv({}))

        assert not r.is_error
        data = _json(r.content)
        assert data['todos'] == []
        assert data['summary']['total'] == 0

    @pytest.mark.asyncio
    async def test_write_replaces_list(self):
        t = _tool()

        r = await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Research', 'status': 'pending'},
                {'id': '2', 'content': 'Implement', 'status': 'in_progress'},
            ],
        }))

        assert not r.is_error
        data = _json(r.content)
        assert [item['id'] for item in data['todos']] == ['1', '2']
        assert data['summary']['total'] == 2
        assert data['summary']['pending'] == 1
        assert data['summary']['in_progress'] == 1
        assert data['summary']['done'] == 0

    @pytest.mark.asyncio
    async def test_replace_dedupes_by_id_keep_last_occurrence(self):
        t = _tool()

        r = await t.execute(_inv({
            'todos': [
                {'id': 'a', 'content': 'Old', 'status': 'pending'},
                {'id': 'b', 'content': 'Other', 'status': 'pending'},
                {'id': 'a', 'content': 'New', 'status': 'completed'},
            ],
        }))

        data = _json(r.content)
        assert data['todos'] == [
            {'id': 'b', 'content': 'Other', 'status': 'pending'},
            {'id': 'a', 'content': 'New', 'status': 'completed'},
        ]

    @pytest.mark.asyncio
    async def test_invalid_status_defaults_to_pending(self):
        t = _tool()

        r = await t.execute(_inv({
            'todos': [{'id': '1', 'content': 'Task', 'status': 'not-real'}],
        }))

        data = _json(r.content)
        assert data['todos'][0]['status'] == 'pending'


class TestMerge:
    @pytest.mark.asyncio
    async def test_merge_updates_existing_and_appends_new(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Research', 'status': 'pending'},
                {'id': '2', 'content': 'Implement', 'status': 'pending'},
            ],
        }))

        r = await t.execute(_inv({
            'merge': True,
            'todos': [
                {'id': '2', 'content': 'Implement API', 'status': 'in_progress'},
                {'id': '3', 'content': 'Test', 'status': 'pending'},
            ],
        }))

        data = _json(r.content)
        assert data['todos'] == [
            {'id': '1', 'content': 'Research', 'status': 'pending'},
            {'id': '2', 'content': 'Implement API', 'status': 'in_progress'},
            {'id': '3', 'content': 'Test', 'status': 'pending'},
        ]

    @pytest.mark.asyncio
    async def test_merge_ignores_items_without_id(self):
        t = _tool()
        await t.execute(_inv({'todos': [{'id': '1', 'content': 'Keep', 'status': 'pending'}]}))

        r = await t.execute(_inv({
            'merge': True,
            'todos': [{'id': '', 'content': 'Ignored', 'status': 'pending'}],
        }))

        data = _json(r.content)
        assert data['todos'] == [{'id': '1', 'content': 'Keep', 'status': 'pending'}]


class TestNewStatuses:
    @pytest.mark.asyncio
    async def test_blocked_and_skipped_accepted(self):
        t = _tool()
        r = await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'A', 'status': 'blocked'},
                {'id': '2', 'content': 'B', 'status': 'skipped'},
            ],
        }))
        data = _json(r.content)
        assert data['todos'][0]['status'] == 'blocked'
        assert data['todos'][1]['status'] == 'skipped'

    @pytest.mark.asyncio
    async def test_summary_includes_blocked_skipped_and_done(self):
        t = _tool()
        r = await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'A', 'status': 'completed'},
                {'id': '2', 'content': 'B', 'status': 'skipped'},
                {'id': '3', 'content': 'C', 'status': 'blocked'},
                {'id': '4', 'content': 'D', 'status': 'pending'},
            ],
        }))
        data = _json(r.content)
        s = data['summary']
        assert s['blocked'] == 1
        assert s['skipped'] == 1
        assert s['done'] == 2  # completed + skipped
        assert s['total'] == 4

    @pytest.mark.asyncio
    async def test_step_numbers_in_injection(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'First', 'status': 'in_progress'},
                {'id': '2', 'content': 'Second', 'status': 'pending'},
            ],
        }))
        injection = t.format_for_injection()
        assert '1.' in injection
        assert '2.' in injection

    @pytest.mark.asyncio
    async def test_blocked_item_appears_in_injection(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Waiting', 'status': 'blocked'},
            ],
        }))
        injection = t.format_for_injection()
        assert injection is not None
        assert 'Waiting' in injection

    @pytest.mark.asyncio
    async def test_skipped_not_in_injection(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Skipped step', 'status': 'skipped'},
                {'id': '2', 'content': 'Active step', 'status': 'pending'},
            ],
        }))
        injection = t.format_for_injection()
        assert 'Skipped step' not in injection
        assert 'Active step' in injection

    @pytest.mark.asyncio
    async def test_progress_counter_in_injection(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Done', 'status': 'completed'},
                {'id': '2', 'content': 'Active', 'status': 'pending'},
            ],
        }))
        injection = t.format_for_injection()
        assert '1/2 steps done' in injection


class TestInjectionFormat:
    @pytest.mark.asyncio
    async def test_format_for_injection_includes_only_active_items(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [
                {'id': '1', 'content': 'Done item', 'status': 'completed'},
                {'id': '2', 'content': 'Current item', 'status': 'in_progress'},
                {'id': '3', 'content': 'Future item', 'status': 'pending'},
                {'id': '4', 'content': 'Cancelled item', 'status': 'cancelled'},
            ],
        }))

        injection = t.format_for_injection()

        assert injection is not None
        assert 'Current item' in injection
        assert 'Future item' in injection
        assert 'Done item' not in injection
        assert 'Cancelled item' not in injection

    @pytest.mark.asyncio
    async def test_format_for_injection_empty_when_no_active_items(self):
        t = _tool()
        await t.execute(_inv({
            'todos': [{'id': '1', 'content': 'Done', 'status': 'completed'}],
        }))

        assert t.format_for_injection() is None


class TestHydration:
    def test_hydrate_from_latest_todo_tool_result(self):
        t = _tool()
        messages = [
            AssistantMessage(contents=[
                ToolCallContent(id='call-1', name='todo', args={}),
            ]),
            ToolMessage(contents=[
                ToolResultContent(
                    id='call-1',
                    content=json.dumps({
                        'todos': [{'id': '1', 'content': 'Old', 'status': 'pending'}],
                        'summary': {},
                    }),
                ),
            ]),
            AssistantMessage(contents=[
                ToolCallContent(id='call-2', name='todo', args={}),
            ]),
            ToolMessage(contents=[
                ToolResultContent(
                    id='call-2',
                    content=json.dumps({
                        'todos': [{'id': '2', 'content': 'Current', 'status': 'in_progress'}],
                        'summary': {},
                    }),
                ),
            ]),
        ]

        t.hydrate_from_messages(messages)

        assert t.store.read() == [{'id': '2', 'content': 'Current', 'status': 'in_progress'}]

    def test_hydrate_ignores_non_todo_results(self):
        t = _tool()
        messages = [
            AssistantMessage(contents=[
                ToolCallContent(id='call-1', name='other', args={}),
            ]),
            ToolMessage(contents=[
                ToolResultContent(
                    id='call-1',
                    content=json.dumps({
                        'todos': [{'id': '1', 'content': 'Wrong', 'status': 'pending'}],
                    }),
                ),
            ]),
        ]

        t.hydrate_from_messages(messages)

        assert t.store.read() == []
