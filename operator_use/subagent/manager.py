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
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable

from operator_use.subagent.pool import TaskPool
from operator_use.subagent.service import Subagent
from operator_use.subagent.types import DeliveryMode, SubagentRecord, SubagentSettings, SubagentStatus

if TYPE_CHECKING:
    from operator_use.bus.service import Bus
    from operator_use.hooks.service import Hooks
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool

from operator_use.subagent.profile import SubagentProfile

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
        self._listeners: defaultdict[str, list[Callable[[SubagentRecord], Awaitable[None]]]] = defaultdict(list)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_profiles(self, profiles: list[SubagentProfile]) -> None:
        """Replace the active profile map (called on settings reload)."""
        self._profiles = {p.name: p for p in profiles}

    def list_profiles(self) -> list[SubagentProfile]:
        """Return all registered subagent profiles."""
        return list(self._profiles.values())

    def get_profile(self, name: str) -> SubagentProfile | None:
        """Look up a profile by name, returning None if not found."""
        return self._profiles.get(name)

    def on_complete(
        self,
        task_id: str,
        callback: Callable[[SubagentRecord], Awaitable[None]],
    ) -> None:
        """Register a one-shot callback fired when task_id completes (any status)."""
        self._listeners[task_id].append(callback)

    async def invoke(
        self,
        task: str,
        label: str | None = None,
        depends_on: list[str] | None = None,
        profile: str | None = None,
        spawn_depth: int = 0,
        fork: bool = False,
        parent_messages: list | None = None,
        parent_system_prompt: str | None = None,
        team_id: str | None = None,
        deliver: DeliveryMode = 'agent',
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """Spawn a background subagent task and return its task_id immediately.

        Channel + chat_id default to asyncio context variables set by
        Gateway._run_session(); pass them explicitly for out-of-band spawns (cron, etc).
        """
        channel = channel or _session_channel.get()
        chat_id = chat_id or _session_chat_id.get()

        if not fork:
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

        resolved = self._profiles.get(profile or '') if profile else None
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
            system_prompt=parent_system_prompt if fork else (resolved.system_prompt if resolved else None),
            tool_names=resolved.tools if resolved and resolved.tools else None,
            spawn_depth=spawn_depth,
            fork=fork,
            parent_messages=parent_messages,
            parent_system_prompt=parent_system_prompt,
            team_id=team_id,
            deliver=deliver,
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
        """Cancel an in-flight task, returning True if the cancellation was sent."""
        t = self._tasks.get(task_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def get_record(self, task_id: str) -> SubagentRecord | None:
        """Retrieve a subagent record by task_id, or None if not found."""
        return self._records.get(task_id)

    def list_all(self) -> list[SubagentRecord]:
        """Return all records sorted by start time, newest first."""
        return sorted(self._records.values(), key=lambda r: r.started_at, reverse=True)

    def pool_stats(self) -> dict:
        """Return task pool statistics (pending, running, completed counts)."""
        return self._pool.stats()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run_and_announce(self, record: SubagentRecord) -> None:
        """Execute the subagent, publish its result, then invoke registered completion callbacks."""
        await self._runner.run(record)
        # Shield the announce from cancellation to ensure results are always published.
        try:
            await asyncio.shield(self._announce(record))
        except Exception:
            logger.exception('[%s] failed to announce result to bus', record.task_id)
        # Fire all registered one-shot completion callbacks, catching exceptions from each.
        for cb in self._listeners.pop(record.task_id, []):
            try:
                await cb(record)
            except Exception:
                logger.warning('[%s] completion listener raised', record.task_id, exc_info=True)

    async def _announce(self, record: SubagentRecord) -> None:
        """Publish the subagent result back to the originating session via the bus.

        Behavior depends on delivery mode: 'channel' sends the raw result as an
        OutgoingMessage; 'agent' wraps it in an IncomingMessage for the LLM to
        summarize. Both CLI (stdio) and gateway channels use the bus; Gateway
        routes 'stdio' messages directly to runtime.current_session.
        """
        # Sanity check: need both channel and chat_id to route the result.
        if not record.channel or not record.chat_id:
            logger.warning(
                '[%s] no channel/chat_id — result dropped. Result:\n%s',
                record.task_id, record.result,
            )
            return
        # Bus required for any delivery mode.
        if self._bus is None:
            logger.warning(
                '[%s] no bus available — result dropped. Result:\n%s',
                record.task_id, record.result,
            )
            return

        result_text = record.result or '(no output)'

        from operator_use.bus.types import IncomingMessage, OutgoingMessage, TextPart

        # 'channel' mode: send raw result directly to the chat.
        if record.deliver == 'channel':
            await self._bus.publish_outgoing(OutgoingMessage(
                channel=record.channel,
                chat_id=record.chat_id,
                parts=[TextPart(content=result_text)],
                metadata={'_subagent_result': True, 'task_id': record.task_id},
            ))
            return

        # 'agent' mode: wrap result in an IncomingMessage for the main agent to summarize.
        status_label = record.status.value
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
        await self._bus.publish_incoming(IncomingMessage(
            channel=record.channel,
            chat_id=record.chat_id,
            parts=[TextPart(content=content)],
            user_id='subagent',
            metadata={'_subagent_result': True, 'task_id': record.task_id},
        ))

    def _check_for_cycles(self, new_id: str, depends_on: list[str]) -> None:
        """Detect and raise ValueError if adding new_id would create a dependency cycle.

        Uses depth-first search to traverse the dependency graph and raise early
        if new_id would depend (directly or transitively) on itself.
        """
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
