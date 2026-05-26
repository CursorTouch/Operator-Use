"""Background skill review — runs in a daemon thread after a turn completes.

After SKILL_NUDGE_INTERVAL tool calls the agent spawns a lightweight sub-engine
that reads the conversation and updates the skill library via skill_manage.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from program.inference.api.text.service import LLM
    from program.message.types import BaseMessage
    from program.tool.types import Tool

logger = logging.getLogger(__name__)

SKILL_NUDGE_INTERVAL = 10  # tool calls before triggering a review

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be "
    "ACTIVE — most sessions produce at least one skill update, even if "
    "small. A pass that does nothing is a missed learning opportunity, "
    "not a neutral outcome.\n\n"
    "Target shape of the library: CLASS-LEVEL skills, each with a rich "
    "SKILL.md and a `references/` directory for session-specific detail. "
    "Not a long flat list of narrow one-session-one-skill entries.\n\n"
    "Signals to look for (any one warrants action):\n"
    "  • User corrected your style, tone, format, or verbosity. "
    "Frustration signals like 'stop doing X', 'this is too verbose', "
    "'just give me the answer', 'you always do Y and I hate it', or an "
    "explicit 'remember this' are FIRST-CLASS skill signals. Update the "
    "relevant skill to embed the preference so the next session starts "
    "already knowing.\n"
    "  • User corrected your workflow, approach, or sequence of steps. "
    "Encode the correction as a pitfall or explicit step in the skill "
    "that governs that class of task.\n"
    "  • A non-trivial technique, fix, workaround, debugging path, or "
    "tool-usage pattern emerged that a future session would benefit from.\n"
    "  • A skill loaded this session turned out to be wrong, missing a "
    "step, or outdated. Patch it now.\n\n"
    "Preference order — prefer the earliest action that fits:\n"
    "  1. PATCH A CURRENTLY-LOADED SKILL. If a skill was read via "
    "skill_view this turn and it covers the new learning, patch it first.\n"
    "  2. PATCH AN EXISTING SKILL. Use skill_view to check if another "
    "skill covers the territory, then patch it.\n"
    "  3. WRITE A SUPPORT FILE under an existing skill:\n"
    "     • references/<topic>.md — error transcripts, reproduction "
    "recipes, domain notes, API quirks.\n"
    "     • templates/<name>.<ext> — boilerplate the agent should copy.\n"
    "     • scripts/<name>.<ext> — deterministic commands to re-run.\n"
    "  4. CREATE A NEW CLASS-LEVEL SKILL when no existing skill covers "
    "the class. The name must be at the class level — not a specific PR "
    "number, error string, or 'fix-X / debug-Y' session artifact.\n\n"
    "User-preference embedding: when the user expressed a style/format/"
    "workflow preference, the lesson belongs in the SKILL.md body, not "
    "just in memory. Skills capture 'how to do this class of task for "
    "this user'.\n\n"
    "Do NOT capture:\n"
    "  • Environment failures: missing binaries, path mismatches, "
    "unconfigured credentials. These are not durable rules.\n"
    "  • Negative claims about tools ('X is broken', 'Y does not work'). "
    "These harden into refusals long after the problem is fixed.\n"
    "  • Transient errors that resolved before the conversation ended.\n"
    "  • One-off task narratives unlikely to recur.\n\n"
    "If the session ran smoothly with no corrections and produced no new "
    "technique, say 'Nothing to save.' and stop. Otherwise, act."
)


def _format_messages_for_review(messages: list[Any]) -> str:
    """Render conversation messages as plain text for the review prompt."""
    from program.message.types import AssistantMessage, UserMessage, TextContent

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
        # skip ToolMessage — too noisy for skill review
    return '\n\n'.join(lines)


async def _run_review(
    llm: LLM,
    messages: list[Any],
    skill_manage_tool: Tool,
    skill_view_tool: Tool | None,
) -> None:
    """Run the review engine: LLM reads the conversation and calls skill_manage."""
    from program.agent.types import AgentContext
    from program.engine.service import Engine
    from program.engine.types import Options
    from program.hooks.types import AgentEndEvent
    from program.message.types import UserMessage, TextContent

    conversation_text = _format_messages_for_review(messages)
    review_task = (
        f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        + _SKILL_REVIEW_PROMPT
    )

    tools: list[Tool] = [skill_manage_tool]
    if skill_view_tool is not None:
        tools.append(skill_view_tool)

    engine = Engine(llm=llm, tools=tools, options=Options())

    ctx = AgentContext(
        system_prompt=(
            "You are a background skill curator. You review conversations and "
            "maintain a library of reusable skills. You have access to skill_manage "
            "to create, edit, and patch skills, and skill_view to read existing ones."
        ),
        messages=[UserMessage(contents=[TextContent(content=review_task)])],
        tools=tools,
    )

    actions: list[str] = []

    async def on_event(event: Any) -> None:
        if isinstance(event, AgentEndEvent):
            for msg in reversed(event.messages):
                from program.message.types import AssistantMessage
                if isinstance(msg, AssistantMessage):
                    text = msg.text_content()
                    if text and text.strip().lower() != 'nothing to save.':
                        actions.append(text.strip())
                    break

    engine.options.on_event = on_event

    try:
        await asyncio.wait_for(engine.run(ctx), timeout=120.0)
    except asyncio.TimeoutError:
        logger.warning('skill review timed out after 120s')
    except Exception as exc:
        logger.warning('skill review error: %s', exc)

    if actions:
        logger.info('skill review completed: %s', actions[0][:120])
    else:
        logger.debug('skill review: nothing to save')


def spawn_skill_review(
    llm: LLM,
    messages: list[Any],
    skill_manage_tool: Tool,
    skill_view_tool: Tool | None = None,
) -> None:
    """Spawn a daemon thread that runs the skill review loop."""

    def _thread_target() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _run_review(llm, messages, skill_manage_tool, skill_view_tool)
            )
        except Exception as exc:
            logger.warning('skill review thread error: %s', exc)
        finally:
            loop.close()

    t = threading.Thread(target=_thread_target, daemon=True, name='skill-review')
    t.start()
    logger.debug('skill review thread spawned')


class SkillReviewTracker:
    """Tracks tool call count and decides when to trigger a review."""

    def __init__(self, interval: int = SKILL_NUDGE_INTERVAL) -> None:
        self._interval = interval
        self._count = 0

    def on_tool_call(self) -> None:
        if self._interval > 0:
            self._count += 1

    def should_review(self) -> bool:
        return self._interval > 0 and self._count >= self._interval

    def reset(self) -> None:
        self._count = 0
