"""SubagentManager — spawns and tracks ephemeral subagent workers.

Each Runtime owns one SubagentManager. When the spawn_agent tool is called,
the manager creates a Subagent, runs it as a background asyncio Task via
TaskPool, and injects the result back into the main session when done.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from program.subagent.pool import TaskPool
from program.subagent.service import Subagent
from program.subagent.types import SubagentRecord, SubagentSettings

if TYPE_CHECKING:
    from program.agent.service import Agent
    from program.agent.types import PromptOptions
    from program.inference.api.text.service import LLM
    from program.runtime.service import Runtime
    from program.tool.types import Tool

logger = logging.getLogger(__name__)


class SubagentManager:
    """Spawns isolated subagent tasks and injects results back into the main session."""

    def __init__(
        self,
        runtime: Runtime,
        llm: LLM,
        tools: list[Tool],
        settings: SubagentSettings | None = None,
    ) -> None:
        self._runtime = runtime
        self._settings = settings or SubagentSettings()
        self._runner = Subagent(llm=llm, tools=tools, settings=self._settings)
        self._pool = TaskPool(max_concurrent=self._settings.max_concurrent)
        self._records: dict[str, SubagentRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def invoke(
        self,
        task: str,
        label: str | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        """Spawn a background subagent. Returns task_id immediately."""
        # Capture the calling agent now — result must go back to this specific agent,
        # not whatever session happens to be active when the subagent finishes.
        calling_agent = self._runtime.current_session
        if calling_agent is None:
            raise RuntimeError('No active agent session to receive the subagent result.')

        task_id = f'sub_{uuid.uuid4().hex[:8]}'
        display_label = label or task[:50]
        depends_on = depends_on or []

        if depends_on:
            self._check_for_cycles(task_id, depends_on)

        record = SubagentRecord(
            task_id=task_id,
            label=display_label,
            task=task,
            status='running',
            started_at=datetime.now(),
            depends_on=depends_on,
        )
        self._records[task_id] = record

        for dep_id in depends_on:
            if dep_id in self._records:
                self._records[dep_id].dependents.append(task_id)

        t = self._pool.submit(
            self._run_and_announce(record, calling_agent),
            task_id,
            depends_on=depends_on,
        )
        self._tasks[task_id] = t
        t.add_done_callback(lambda _: self._tasks.pop(task_id, None))

        return task_id

    def cancel(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def get_record(self, task_id: str) -> SubagentRecord | None:
        return self._records.get(task_id)

    def list_all(self) -> list[SubagentRecord]:
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    def pool_stats(self) -> dict:
        return self._pool.stats()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run_and_announce(self, record: SubagentRecord, agent: Agent) -> None:
        await self._runner.run(record)
        await self._announce(record, agent)

    async def _announce(self, record: SubagentRecord, agent: Agent) -> None:
        """Inject the result directly into the agent that spawned this subagent."""
        from program.agent.types import PromptOptions

        status_line = 'completed' if record.status == 'completed' else record.status
        result_text = record.result or f'(subagent {record.status})'
        msg = (
            f'[Subagent result — task_id={record.task_id} label="{record.label}" status={status_line}]\n\n'
            f'{result_text}\n\n'
            f'Summarize this result for the user naturally in 1-2 sentences. '
            f'Do not mention technical terms like "subagent" or task IDs.'
        )

        # Wait until the specific calling agent is idle before injecting.
        for _ in range(600):  # up to 60 seconds
            try:
                await agent.invoke(msg, PromptOptions(source='subagent'))
                return
            except RuntimeError:
                await asyncio.sleep(0.1)

        logger.warning('[%s] could not inject result — agent stayed busy', record.task_id)

    def _check_for_cycles(self, new_id: str, depends_on: list[str]) -> None:
        visited: set[str] = set()
        stack = list(depends_on)
        while stack:
            dep_id = stack.pop()
            if dep_id == new_id:
                raise ValueError(
                    f'Circular dependency: {new_id} depends on {dep_id} which depends back on {new_id}.'
                )
            if dep_id in visited:
                continue
            visited.add(dep_id)
            if dep_id in self._records:
                stack.extend(self._records[dep_id].depends_on)
