"""Subagent — ephemeral, anonymous worker that executes a single delegated task.

A subagent has no session, no memory, and no extensions.
It is a blank Engine + LLM loop: task in → result out → discarded.
Hooks receive SubagentStartEvent and SubagentEndEvent for lifecycle observability.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

from operator_use.agent.types import AgentContext
from operator_use.engine.service import Engine
from operator_use.engine.types import Options
from operator_use.hooks.types import AgentEndEvent, AgentErrorEvent, SubagentEndEvent, SubagentStartEvent, TurnEndEvent
from operator_use.message.types import (
    AssistantMessage, CompactionSummaryMessage, LLMMessage, TextContent,
    ToolMessage, UserMessage,
)
from operator_use.subagent.types import SubagentRecord, SubagentSettings, SubagentStatus

if TYPE_CHECKING:
    from operator_use.hooks.service import Hooks
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    'You are a focused subagent. A task has been delegated to you by the main agent.\n'
    'Complete the task using your available tools. When finished, provide a clear, '
    'complete summary of your findings or results. Do not address the user directly — '
    'your final response is relayed by the main agent.\n\n'
    'Always use absolute paths with file tools. Relative paths resolve against the '
    'current working directory, not against any profile or knowledge directory.'
)


def _build_system_prompt(custom: str | None = None) -> str:
    """Append the current working directory to the base (or custom) system prompt."""
    base = custom or _DEFAULT_SYSTEM_PROMPT
    return base + f'\n\nCurrent working directory: {os.getcwd()}'


def _build_fork_messages(
    parent_messages: list,
    task: str,
) -> list[LLMMessage]:
    """Convert parent session messages into LLM-compatible messages for a forked subagent.

    CompactionSummaryMessage is converted to a UserMessage so the fork sees the
    summary text. BranchSummaryMessage and CustomMessage are dropped — they carry
    no actionable content for the fork. Regular LLM messages pass through unchanged.
    """
    result = []
    for msg in parent_messages:
        if isinstance(msg, CompactionSummaryMessage):
            result.append(UserMessage.text(
                f'[Previous conversation summary]\n{msg.summary}'
            ))
        elif isinstance(msg, (UserMessage, AssistantMessage, ToolMessage)):
            result.append(msg)
    result.append(UserMessage.text(task))
    return result


class Subagent:
    """Ephemeral worker that runs an isolated Engine loop for one delegated task."""

    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        settings: SubagentSettings,
        hooks: Hooks | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._settings = settings
        self._hooks = hooks

    async def run_single(
        self,
        task: str,
        system_prompt: str | None = None,
        tools: list[Tool] | None = None,
        spawn_depth: int = 1,
    ) -> str:
        """Run an isolated engine loop without record tracking or SubagentStartEvent/EndEvent emissions.

        Useful for internal use (e.g., in skills) where hooks and record overhead are unwanted.
        """
        allowed_tools = tools if tools is not None else [t for t in self._tools if t.name != 'subagent']
        return await self._run_loop(
            task=task,
            system_prompt=_build_system_prompt(system_prompt),
            tools=allowed_tools,
            max_iterations=self._settings.max_iterations,
            spawn_depth=spawn_depth,
        )

    async def run(self, record: SubagentRecord) -> None:
        """Execute the task in record, updating its status and result in-place, emitting hook events."""
        logger.info('[%s] subagent "%s" started', record.task_id, record.label)

        if self._hooks:
            await self._hooks.emit(SubagentStartEvent(
                task_id=record.task_id,
                label=record.label,
                task=record.task,
            ))

        # Filter tools: exclude 'subagent' (prevent spawning subagents), then restrict to allow-list if set.
        allowed_tools = [t for t in self._tools if t.name != 'subagent']
        if record.tool_names is not None:
            allowed_set = set(record.tool_names)
            allowed_tools = [t for t in allowed_tools if t.name in allowed_set]

        system_prompt = _build_system_prompt(record.system_prompt)
        max_iterations = self._settings.max_iterations
        timeout = self._settings.timeout

        result = '(no result)'

        # For forked subagents, convert parent messages to LLM format.
        fork_messages = None
        if record.fork and record.parent_messages is not None:
            fork_messages = _build_fork_messages(record.parent_messages, record.task)

        # Retry loop with exponential backoff on failure.
        for attempt in range(self._settings.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._run_loop(
                        record.task, system_prompt, allowed_tools, max_iterations,
                        spawn_depth=record.spawn_depth,
                        fork_messages=fork_messages,
                    ),
                    timeout=timeout,
                )
                record.status = SubagentStatus.completed
                break

            except asyncio.CancelledError:
                logger.info('[%s] subagent "%s" cancelled', record.task_id, record.label)
                record.status = SubagentStatus.cancelled
                result = '(cancelled)'
                break

            except asyncio.TimeoutError:
                logger.warning('[%s] subagent "%s" timed out after %.0fs', record.task_id, record.label, timeout)
                result = f'(timed out after {timeout:.0f}s)'
                record.status = SubagentStatus.failed
                break

            except Exception as exc:
                # Retry with exponential backoff, capped at retry_max_delay.
                record.retry_count = attempt
                if attempt < self._settings.max_retries:
                    delay = min(
                        self._settings.retry_base_delay * (self._settings.retry_backoff_factor ** attempt),
                        self._settings.retry_max_delay,
                    )
                    logger.warning(
                        '[%s] attempt %d failed: %s; retrying in %.1fs',
                        record.task_id, attempt + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    result = f'(error: {type(exc).__name__}: {exc})'
                    record.status = SubagentStatus.failed
                    logger.error('[%s] subagent "%s" failed: %s', record.task_id, record.label, exc)

        record.result = result
        record.finished_at = datetime.now()
        logger.info('[%s] subagent "%s" done — status=%s', record.task_id, record.label, record.status)

        # Emit end event for observability (e.g., logging, metrics).
        if self._hooks:
            await self._hooks.emit(SubagentEndEvent(
                task_id=record.task_id,
                label=record.label,
                status=record.status,
                result=record.result,
            ))

    async def _run_loop(
        self,
        task: str,
        system_prompt: str,
        tools: list[Tool],
        max_iterations: int,
        spawn_depth: int = 0,
        fork_messages: list[LLMMessage] | None = None,
    ) -> str:
        """Run an isolated engine loop and return the final assistant text output.

        Uses on_event callbacks to track the last AssistantMessage, abort on
        max_iterations, and detect engine errors.
        """
        engine = Engine(llm=self._llm, tools=tools, options=Options())
        engine.tool_context.spawn_depth = spawn_depth

        final_text = ''
        turns = 0
        error: str | None = None

        # Capture final assistant text, track iteration count, and detect errors.
        async def on_event(event) -> None:
            nonlocal final_text, turns, error
            if isinstance(event, AgentEndEvent):
                # Extract the latest assistant message from the turn.
                for msg in reversed(event.messages):
                    if isinstance(msg, AssistantMessage):
                        final_text = msg.text_content()
                        break
            elif isinstance(event, TurnEndEvent):
                # Abort the engine if we've hit the iteration limit.
                turns += 1
                if turns >= max_iterations:
                    engine.abort()
            elif isinstance(event, AgentErrorEvent):
                error = event.error

        engine.options.on_event = on_event

        # Use fork_messages if provided (for forked subagents), else start fresh.
        messages: list[LLMMessage]
        if fork_messages is not None:
            messages = fork_messages
        else:
            messages = [UserMessage(contents=[TextContent(content=task)])]

        ctx = AgentContext(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
        )
        await engine.run(ctx)

        if error:
            raise RuntimeError(error)

        return final_text
