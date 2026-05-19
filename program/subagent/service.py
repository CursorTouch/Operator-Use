"""Subagent — ephemeral, anonymous worker that executes a single delegated task.

A subagent has no session, no memory, and no extensions.
It is a blank Engine + LLM loop: task in → result out → discarded.
Hooks receive SubagentStartEvent and SubagentEndEvent for lifecycle observability.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from program.agent.types import AgentContext
from program.engine.service import Engine
from program.engine.types import Options
from program.hooks.types import AgentEndEvent, AgentErrorEvent, SubagentEndEvent, SubagentStartEvent, TurnEndEvent
from program.message.types import AssistantMessage, TextContent, UserMessage
from program.subagent.types import SubagentRecord, SubagentSettings, SubagentStatus

if TYPE_CHECKING:
    from program.hooks.service import Hooks
    from program.inference.api.text.service import LLM
    from program.tool.types import Tool

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    'You are a focused subagent. A task has been delegated to you by the main agent.\n'
    'Complete the task using your available tools. When finished, provide a clear, '
    'complete summary of your findings or results. Do not address the user directly — '
    'your final response is relayed by the main agent.'
)


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

    async def run(self, record: SubagentRecord) -> None:
        logger.info('[%s] subagent "%s" started', record.task_id, record.label)

        if self._hooks:
            await self._hooks.emit(SubagentStartEvent(
                task_id=record.task_id,
                label=record.label,
                task=record.task,
            ))

        allowed_tools = [t for t in self._tools if t.name != 'subagent']

        system_prompt = self._settings.system_prompt or _DEFAULT_SYSTEM_PROMPT
        max_iterations = self._settings.max_iterations
        timeout = self._settings.timeout

        result = '(no result)'

        for attempt in range(self._settings.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._run_loop(record.task, system_prompt, allowed_tools, max_iterations),
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
    ) -> str:
        engine = Engine(llm=self._llm, tools=tools, options=Options())

        final_text = ''
        turns = 0
        error: str | None = None

        async def on_event(event) -> None:
            nonlocal final_text, turns, error
            if isinstance(event, AgentEndEvent):
                for msg in reversed(event.messages):
                    if isinstance(msg, AssistantMessage):
                        final_text = msg.text_content()
                        break
            elif isinstance(event, TurnEndEvent):
                turns += 1
                if turns >= max_iterations:
                    engine.abort()
            elif isinstance(event, AgentErrorEvent):
                error = event.error

        engine.options.on_event = on_event

        ctx = AgentContext(
            system_prompt=system_prompt,
            messages=[UserMessage(contents=[TextContent(content=task)])],
            tools=tools,
        )
        await engine.run(ctx)

        if error:
            raise RuntimeError(error)

        return final_text
