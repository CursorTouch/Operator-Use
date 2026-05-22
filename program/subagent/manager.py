"""SubagentManager — spawns and tracks ephemeral subagent workers.

Each Runtime owns one SubagentManager. When the subagent tool is called,
the manager creates a Subagent, runs it as a background asyncio Task via
TaskPool, and publishes the result back to the bus as an IncomingMessage
so it flows through the normal Gateway → Agent pipeline back to the
calling session. This is identical to how cron jobs deliver their results.

Channel + chat_id are captured from the asyncio context variable set by the
Gateway just before it invokes each session's agent, so the subagent knows
exactly which conversation to route the result back to.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from program.subagent.pool import TaskPool
from program.subagent.service import Subagent
from program.subagent.types import SubagentRecord, SubagentSettings, SubagentStatus

if TYPE_CHECKING:
    from program.bus.service import Bus
    from program.hooks.service import Hooks
    from program.inference.api.text.service import LLM
    from program.tool.types import Tool

from program.subagent.profile import SubagentProfile

logger = logging.getLogger(__name__)

# Set by Gateway._run_session() before invoking each session agent so that
# any subagent spawned during that turn knows where to deliver its result.
_session_channel: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_session_channel', default=None
)
_session_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_session_chat_id', default=None
)
# Channel-side message ID of the user message that triggered this turn.
# Used by the send tool's react mode to add an emoji reaction to that message.
_session_message_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_session_message_id', default=None
)


class SubagentManager:
    """Spawns isolated subagent tasks and delivers results back via the bus."""

    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        bus: Bus | None = None,
        settings: SubagentSettings | None = None,
        hooks: Hooks | None = None,
        profiles: list[SubagentProfile] | None = None,
    ) -> None:
        self._settings = settings or SubagentSettings()
        self._runner = Subagent(llm=llm, tools=tools, settings=self._settings, hooks=hooks)
        self._pool = TaskPool(max_concurrent=self._settings.max_concurrent)
        self._records: dict[str, SubagentRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._bus: Bus | None = bus
        self._profiles: dict[str, SubagentProfile] = {p.name: p for p in (profiles or [])}

    # ── Public API ────────────────────────────────────────────────────────────

    def update_profiles(self, profiles: list[SubagentProfile]) -> None:
        self._profiles = {p.name: p for p in profiles}

    def list_profiles(self) -> list[SubagentProfile]:
        return list(self._profiles.values())

    def get_profile(self, name: str) -> SubagentProfile | None:
        return self._profiles.get(name)

    async def invoke(
        self,
        task: str,
        label: str | None = None,
        depends_on: list[str] | None = None,
        profile: str | None = None,
    ) -> str:
        """Spawn a background subagent. Returns task_id immediately.

        The channel + chat_id are read from the asyncio context variable set
        by Gateway._run_session(). In CLI mode both will be None and the
        result falls back to a direct agent.invoke() call.
        """
        channel = _session_channel.get()
        chat_id = _session_chat_id.get()

        if not profile:
            raise ValueError("A profile is required. Use list_profiles() to see available options.")
        if profile not in self._profiles:
            raise ValueError(
                f"Unknown subagent profile '{profile}'. "
                f"Available: {', '.join(self._profiles) or 'none'}"
            )

        task_id = f'sub_{uuid.uuid4().hex[:8]}'
        display_label = label or task[:50]
        depends_on = depends_on or []

        if depends_on:
            self._check_for_cycles(task_id, depends_on)

        resolved = self._profiles[profile]
        record = SubagentRecord(
            task_id=task_id,
            label=display_label,
            task=task,
            status=SubagentStatus.running,
            started_at=datetime.now(),
            channel=channel,
            chat_id=chat_id,
            depends_on=depends_on,
            profile=profile,
            system_prompt=resolved.system_prompt,
            tool_names=resolved.tools if resolved.tools else None,
        )
        self._records[task_id] = record

        for dep_id in depends_on:
            if dep_id in self._records:
                self._records[dep_id].dependents.append(task_id)

        t = self._pool.submit(
            self._run_and_announce(record),
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

    async def _run_and_announce(self, record: SubagentRecord) -> None:
        await self._runner.run(record)
        try:
            await asyncio.shield(self._announce(record))
        except Exception:
            logger.exception('[%s] failed to announce result to bus', record.task_id)

    async def _announce(self, record: SubagentRecord) -> None:
        """Deliver the subagent result back to the originating session via the bus.

        Both CLI (stdio) and gateway channels use the same gateway bus.
        Gateway._handle_incoming routes 'stdio' messages directly to
        runtime.current_session so the REPL's own renderer handles output.
        """
        if not record.channel or not record.chat_id:
            logger.warning(
                '[%s] no channel/chat_id — result dropped. Result:\n%s',
                record.task_id, record.result,
            )
            return

        status_label = record.status.value
        result_text = record.result or f'(no output)'
        if record.status == SubagentStatus.completed:
            content = (
                f'[Subagent result — task_id={record.task_id} label="{record.label}" status={status_label}]\n\n'
                f'{result_text}\n\n'
                f'Tell the user: task {record.task_id} ("{record.label}") completed. '
                f'Then summarize the result in 1-2 sentences.'
            )
        else:
            content = (
                f'[Subagent result — task_id={record.task_id} label="{record.label}" status={status_label}]\n\n'
                f'{result_text}\n\n'
                f'Tell the user: task {record.task_id} ("{record.label}") {status_label}. '
                f'Briefly explain what went wrong based on the output above.'
            )

        if self._bus is not None:
            from program.bus.types import IncomingMessage, TextPart
            await self._bus.publish_incoming(IncomingMessage(
                channel=record.channel,
                chat_id=record.chat_id,
                parts=[TextPart(content=content)],
                user_id='subagent',
                metadata={'_subagent_result': True, 'task_id': record.task_id},
            ))
        else:
            logger.warning(
                '[%s] no bus available — result dropped. Result:\n%s',
                record.task_id, record.result,
            )

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
