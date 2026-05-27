from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from program.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


TodoStatus = Literal['pending', 'in_progress', 'completed', 'cancelled', 'blocked', 'skipped']
VALID_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled', 'blocked', 'skipped'}

_STATUS_ICON = {
    'pending':     '○',
    'in_progress': '◉',
    'completed':   '✓',
    'cancelled':   '✗',
    'blocked':     '⊘',
    'skipped':     '—',
}


class TodoItemSchema(BaseModel):
    id: str = Field(description='Unique item identifier.')
    content: str = Field(description='Task description.')
    status: TodoStatus = Field(description='Current item status.')


class TodoSchema(BaseModel):
    todos: list[TodoItemSchema] | None = Field(
        default=None,
        description='Task items to write. Omit to read the current list.',
    )
    merge: bool = Field(
        default=False,
        description='If true, update existing items by id and append new items. If false, replace the entire list.',
    )


@dataclass
class TodoStore:
    """In-memory todo list. One instance per TodoTool."""

    _items: list[dict[str, str]] = field(default_factory=list)

    def write(self, todos: list[dict[str, Any]], merge: bool = False) -> list[dict[str, str]]:
        if not merge:
            self._items = [self._validate(item) for item in self._dedupe_by_id(todos)]
            return self.read()

        existing = {item['id']: item for item in self._items}
        for item in self._dedupe_by_id(todos):
            item_id = str(item.get('id', '')).strip()
            if not item_id:
                continue

            if item_id in existing:
                if item.get('content'):
                    existing[item_id]['content'] = str(item['content']).strip()
                if item.get('status'):
                    status = str(item['status']).strip().lower()
                    if status in VALID_STATUSES:
                        existing[item_id]['status'] = status
            else:
                validated = self._validate(item)
                existing[validated['id']] = validated
                self._items.append(validated)

        seen: set[str] = set()
        rebuilt: list[dict[str, str]] = []
        for item in self._items:
            current = existing.get(item['id'], item)
            if current['id'] not in seen:
                rebuilt.append(current)
                seen.add(current['id'])
        self._items = rebuilt
        return self.read()

    def read(self) -> list[dict[str, str]]:
        return [item.copy() for item in self._items]

    def format_for_injection(self) -> str | None:
        active_statuses = {'pending', 'in_progress', 'blocked'}
        active_items = [
            (i + 1, item)
            for i, item in enumerate(self._items)
            if item['status'] in active_statuses
        ]
        if not active_items:
            return None

        done = sum(1 for item in self._items if item['status'] in {'completed', 'skipped'})
        lines = [
            '[Your active task list was preserved across context compaction]',
            f'Progress: {done}/{len(self._items)} steps done',
        ]
        for step, item in active_items:
            icon = _STATUS_ICON.get(item['status'], '?')
            lines.append(f'  {step}. [{icon}] {item["content"]}  ({item["status"]})')
        return '\n'.join(lines)

    @staticmethod
    def _validate(item: dict[str, Any]) -> dict[str, str]:
        item_id = str(item.get('id', '')).strip() or '?'
        content = str(item.get('content', '')).strip() or '(no description)'
        status = str(item.get('status', 'pending')).strip().lower()
        if status not in VALID_STATUSES:
            status = 'pending'
        return {'id': item_id, 'content': content, 'status': status}

    @staticmethod
    def _dedupe_by_id(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_index: dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get('id', '')).strip() or '?'
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


def _summary(items: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {s: 0 for s in ('pending', 'in_progress', 'completed', 'cancelled', 'blocked', 'skipped')}
    for item in items:
        status = item['status']
        if status in counts:
            counts[status] += 1
    done = counts['completed'] + counts['skipped']
    return {'total': len(items), 'done': done, **counts}


class TodoTool(Tool):
    def __init__(self, store: TodoStore | None = None) -> None:
        super().__init__(
            name='todo',
            description=(
                'Manage your ordered task list for the current session. Use for complex tasks '
                'with 3+ steps or when the user provides multiple tasks. '
                'Call with no parameters to read the current list. '
                'Provide todos to create/update items. '
                'List order defines step order. Only one item should be in_progress at a time. '
                'Statuses: pending, in_progress, completed, cancelled, blocked, skipped. '
                'Mark items completed immediately when done. '
                'Use blocked when waiting on something external. '
                'Always returns the full current list with progress summary.'
            ),
            schema=TodoSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,        )
        self.store = store or TodoStore()

    def format_for_injection(self) -> str | None:
        return self.store.format_for_injection()

    def hydrate_from_messages(self, messages: list[Any]) -> None:
        """Rebuild the store from the latest persisted todo tool result."""
        from program.message.types import AssistantMessage, ToolCallContent, ToolMessage, ToolResultContent

        todo_call_ids: set[str] = set()
        latest_todos: list[dict[str, Any]] | None = None

        for message in messages:
            if isinstance(message, AssistantMessage):
                for content in message.contents:
                    if (
                        isinstance(content, ToolCallContent)
                        and content.name == self.name
                        and content.id
                    ):
                        todo_call_ids.add(content.id)
            elif isinstance(message, ToolMessage):
                for content in message.contents:
                    if (
                        isinstance(content, ToolResultContent)
                        and content.id in todo_call_ids
                        and not content.is_error
                    ):
                        try:
                            data = json.loads(content.content)
                        except json.JSONDecodeError:
                            continue
                        todos = data.get('todos') if isinstance(data, dict) else None
                        if isinstance(todos, list):
                            latest_todos = [item for item in todos if isinstance(item, dict)]

        if latest_todos is not None:
            self.store.write(latest_todos, merge=False)

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        todos = params.get('todos')
        merge = bool(params.get('merge', False))

        if todos is not None:
            if not isinstance(todos, list):
                return ToolResult.error(id=invocation.id, content="'todos' must be a list when provided.")
            items = self.store.write(todos, merge=merge)
        else:
            items = self.store.read()

        return ToolResult.ok(
            id=invocation.id,
            content=json.dumps(
                {
                    'todos': items,
                    'summary': _summary(items),
                },
                ensure_ascii=False,
            ),
        )


tool = TodoTool()
