"""Background memory review — runs in a daemon thread after a turn completes.

After MEMORY_NUDGE_INTERVAL tool calls the agent spawns a lightweight sub-engine
that reads the conversation and saves relevant facts to memory via the memory tool.
Only runs when a memory provider is active.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.tool.types import Tool
    from operator_use.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

MEMORY_NUDGE_INTERVAL = 10  # tool calls before triggering a review

_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving facts to memory.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, role, "
    "goals, preferences, or personal details worth remembering across sessions?\n"
    "2. Has the user expressed expectations about how you should behave, their "
    "working style, communication preferences, or ways they want you to operate?\n"
    "3. Are there durable project facts — decisions, constraints, conventions, "
    "or context — that will remain relevant in future sessions?\n\n"
    "Do NOT save:\n"
    "  • Raw conversation transcripts or task summaries — only stable facts.\n"
    "  • Transient state: current task progress, errors that resolved, "
    "temporary choices made for this session only.\n"
    "  • Things already covered by skills — workflow patterns belong in skills, "
    "not memory.\n\n"
    "If something stands out, save it using the memory tool with action=remember. "
    "Keep each memory atomic — one fact per remember call. "
    "If nothing is worth saving, say 'Nothing to save.' and stop."
)


def _format_messages_for_review(messages: list[Any]) -> str:
    from operator_use.message.types import AssistantMessage, UserMessage, TextContent

    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            text = ''.join(
                c.content for c in msg.contents if isinstance(c, TextContent)
            )
            if text:
                lines.append(f"User: {text}")
        elif isinstance(msg, AssistantMessage):
            text = msg.text_content()
            if text:
                lines.append(f"Assistant: {text}")
    return '\n\n'.join(lines)


async def _run_review(
    llm: LLM,
    messages: list[Any],
    memory_tool: Tool,
    memory_manager: MemoryManager,
) -> None:
    from operator_use.agent.types import AgentContext
    from operator_use.engine.service import Engine
    from operator_use.engine.types import Options
    from operator_use.hooks.types import AgentEndEvent
    from operator_use.message.types import UserMessage, TextContent

    conversation_text = _format_messages_for_review(messages)
    review_task = (
        f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        + _MEMORY_REVIEW_PROMPT
    )

    tools: list[Tool] = [memory_tool]
    engine = Engine(llm=llm, tools=tools, options=Options())
    engine.tool_context.memory_manager = memory_manager

    ctx = AgentContext(
        system_prompt=(
            "You are a background memory curator. You review conversations and "
            "save durable facts about the user and project to long-term memory. "
            "You have access to the memory tool to store facts."
        ),
        messages=[UserMessage(contents=[TextContent(content=review_task)])],
        tools=tools,
    )

    saved: list[str] = []

    async def on_event(event: Any) -> None:
        if isinstance(event, AgentEndEvent):
            for msg in reversed(event.messages):
                from operator_use.message.types import AssistantMessage
                if isinstance(msg, AssistantMessage):
                    text = msg.text_content()
                    if text and text.strip().lower() != 'nothing to save.':
                        saved.append(text.strip())
                    break

    engine.options.on_event = on_event

    _MAX_RETRIES = 3
    _BACKOFF = [2.0, 5.0, 15.0]  # seconds between attempts

    for attempt in range(_MAX_RETRIES):
        try:
            await asyncio.wait_for(engine.run(ctx), timeout=60.0)
            break
        except asyncio.TimeoutError:
            logger.warning('memory review timed out after 60s (attempt %d/%d)', attempt + 1, _MAX_RETRIES)
            break  # timeout is not retryable — the review is too slow
        except Exception as exc:
            err = str(exc).lower()
            retryable = any(k in err for k in ('rate limit', '429', '503', '500', 'overloaded', 'timeout'))
            if retryable and attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF[attempt]
                logger.warning('memory review error (attempt %d/%d), retrying in %.0fs: %s', attempt + 1, _MAX_RETRIES, delay, exc)
                await asyncio.sleep(delay)
                engine.reset()  # clear partial state before retry
            else:
                logger.warning('memory review failed: %s', exc)
                break

    if saved:
        logger.info('memory review completed: %s', saved[0][:120])
    else:
        logger.debug('memory review: nothing to save')


def spawn_memory_review(
    llm: LLM,
    messages: list[Any],
    memory_tool: Tool,
    memory_manager: MemoryManager,
    on_complete: Callable[[], None] | None = None,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Spawn a daemon thread that runs the memory review loop."""

    def _thread_target() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _run_review(llm, messages, memory_tool, memory_manager)
            )
        except Exception as exc:
            logger.warning('memory review thread error: %s', exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    pass
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

    t = threading.Thread(target=_thread_target, daemon=True, name='memory-review')
    t.start()
    logger.debug('memory review thread spawned')


class MemoryReviewTracker:
    """Tracks tool call count and decides when to trigger a memory review."""

    def __init__(self, interval: int = MEMORY_NUDGE_INTERVAL) -> None:
        self._interval = interval
        self._count = 0
        self._running = False  # prevents concurrent review threads

    def on_tool_call(self) -> None:
        if self._interval > 0:
            self._count += 1

    def should_review(self) -> bool:
        return self._interval > 0 and self._count >= self._interval and not self._running

    def reset(self) -> None:
        self._count = 0
        self._running = True

    def on_review_done(self) -> None:
        self._running = False
