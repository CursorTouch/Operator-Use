"""SubagentManager — spawns and tracks ephemeral subagent workers.

Each Agent owns one SubagentManager.  When the ``subagents`` tool is called,
the manager creates a fresh Subagent, runs it as a background asyncio Task, and
tracks its record until completion.  The Subagent itself is discarded once done;
only the SubagentRecord (result, status, timestamps) is kept in history.

This is distinct from local-agent delegation (``localagents`` tool), which
forwards tasks to persistent Agent peers already running in the Orchestrator.
See ``subagent/service.py`` for the full design rationale.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from operator_use.bus import Bus
from operator_use.subagent.views import SubagentRecord
from operator_use.subagent.pool import TaskPool

if TYPE_CHECKING:
    from operator_use.providers.base import BaseChatLLM
    from operator_use.config.service import SubagentConfig
    from operator_use.tracing import Tracer

logger = logging.getLogger(__name__)


class SubagentManager:
    """Registry that spawns isolated subagent tasks and tracks their lifecycle.

    Supports task dependency tracking (DAG): tasks can declare dependencies on other
    task_ids and will be deferred until their prerequisites complete.
    """

    def __init__(
        self,
        llm: "BaseChatLLM",
        bus: Bus,
        config: "SubagentConfig | None" = None,
        tracer: "Tracer | None" = None,
    ) -> None:
        from operator_use.subagent.service import Subagent

        self._runner = Subagent(llm=llm, bus=bus, config=config, tracer=tracer)
        self._tasks: dict[str, asyncio.Task] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self._records: dict[str, SubagentRecord] = {}

        # TaskPool for parallel execution control
        max_concurrent = config.max_concurrent if config else 10
        self._pool = TaskPool(max_concurrent=max_concurrent)
        logger.info(f"SubagentManager: parallel execution limit = {max_concurrent}")

        # Keep _events for backward compatibility and explicit completion tracking
        self._events: dict[str, asyncio.Event] = {}  # task_id -> Event fired on completion

    async def ainvoke(
        self,
        task: str,
        label: str | None,
        channel: str,
        chat_id: str,
        account_id: str = "",
        depends_on: list[str] | None = None,
    ) -> str:
        """Spawn a background subagent. Returns task_id immediately.

        Args:
            task: Full task description
            label: Short label for the task
            channel: Origin channel name
            chat_id: Origin chat ID
            account_id: Origin account ID
            depends_on: Optional list of task_ids that must complete before this task starts.
                       If any dependency fails and fail_fast=True, this task is cancelled before launch.
        """
        task_id = f"sub_{uuid.uuid4().hex[:8]}"
        session_key = f"{channel}:{chat_id}"
        display_label = label or task[:50]
        depends_on = depends_on or []

        # Check for cycles in the dependency graph
        if depends_on:
            self._check_for_cycles(task_id, depends_on)

        record = SubagentRecord(
            task_id=task_id,
            label=display_label,
            task=task,
            channel=channel,
            chat_id=chat_id,
            account_id=account_id,
            status="running",
            started_at=datetime.now(),
            depends_on=depends_on,
        )
        self._records[task_id] = record

        # Register completion event for this task
        self._events[task_id] = asyncio.Event()

        # Register this task as a dependent of its prerequisites
        for dep_id in depends_on:
            if dep_id in self._records:
                self._records[dep_id].dependents.append(task_id)

        # Submit to task pool (handles dependencies + concurrency limiting)
        # Pool will wait for depends_on tasks to complete before running
        # and will respect max_concurrent limit
        t = self._pool.submit(
            self._runner.run(record),
            task_id,
            depends_on=depends_on,
        )

        self._tasks[task_id] = t
        self._session_tasks.setdefault(session_key, set()).add(task_id)
        t.add_done_callback(lambda _: self._cleanup(task_id, session_key))
        return task_id

    def _notify_completion(self, task_id: str) -> None:
        """Signal that a task has completed so dependents can proceed."""
        if task_id in self._events:
            self._events[task_id].set()

    def _check_for_cycles(self, new_id: str, depends_on: list[str]) -> None:
        """Check if adding this dependency would create a cycle. Raises ValueError if so."""
        visited = set()
        stack = list(depends_on)

        while stack:
            dep_id = stack.pop()
            if dep_id == new_id:
                raise ValueError(
                    f"Circular dependency detected: task {new_id} depends on "
                    f"{dep_id} which (transitively) depends on {new_id}"
                )
            if dep_id in visited:
                continue
            visited.add(dep_id)

            # Add transitive dependencies to stack
            if dep_id in self._records:
                stack.extend(self._records[dep_id].depends_on)

    def cancel(self, task_id: str) -> bool:
        """Cancel a running subagent. Returns True if it was running."""
        t = self._tasks.get(task_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def cancel_by_session(self, session_key: str) -> int:
        """Cancel all running subagents for a session. Returns count cancelled."""
        count = 0
        for task_id in list(self._session_tasks.get(session_key, [])):
            if self.cancel(task_id):
                count += 1
        return count

    def get_record(self, task_id: str) -> SubagentRecord | None:
        """Return a single record by task_id."""
        return self._records.get(task_id)

    def list_all(self) -> list[SubagentRecord]:
        """Return all records (running + finished), newest first."""
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    def _cleanup(self, task_id: str, session_key: str) -> None:
        """Remove from active tracking - record stays in history."""
        self._notify_completion(task_id)
        self._tasks.pop(task_id, None)
        s = self._session_tasks.get(session_key)
        if s:
            s.discard(task_id)

    def get_pool_stats(self) -> dict:
        """Return task pool statistics (pending, running, completed)."""
        return self._pool.stats()
