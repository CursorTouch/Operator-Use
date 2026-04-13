"""Subagent — ephemeral, anonymous worker that executes a single delegated task.

Design contract
---------------
A subagent is the opposite of a local agent:

* **No identity** — no workspace, no session store, no memory, no hooks, no
  plugins.  It is a blank executor: system prompt + tool registry + loop →
  result → gone.
* **No persistence** — once the task finishes (or fails) the Subagent instance
  is discarded.  Nothing it does is remembered beyond the result string it
  returns to the caller via the bus.
* **Globally configured** — all subagents spawned by a given Agent share the
  same behaviour, controlled by ``agents.defaults.subagent`` in config (tool
  profile, system prompt override, max iterations).

When to use a subagent vs a local agent
-----------------------------------------
Use a **subagent** (``subagents`` tool) when:
  - The task is self-contained and its result is all that matters.
  - You want true parallelism — multiple subagents run concurrently and each
    announces its result back to the session when done.
  - You don't need the worker to remember anything after the task.

Use a **local agent** (``localagents`` tool) when:
  - The target is a named, persistent peer already running in the Orchestrator.
  - The peer has its own workspace, memory, and specialised tools/plugins.
  - You want to delegate to a known capability (e.g. a "research" or "coding"
    agent defined in config) rather than spinning up an anonymous worker.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from operator_use.agent.tools import ToolRegistry
from operator_use.agent.tools.builtin import FILESYSTEM_TOOLS, WEB_TOOLS, TERMINAL_TOOLS
from operator_use.bus import Bus, IncomingMessage
from operator_use.bus.views import TextPart
from operator_use.messages.service import SystemMessage, HumanMessage, ToolMessage
from operator_use.providers.events import LLMEventType
from operator_use.subagent.views import SubagentRecord

if TYPE_CHECKING:
    from operator_use.providers.base import BaseChatLLM
    from operator_use.config.service import SubagentConfig
    from operator_use.tracing import Tracer

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 20

DEFAULT_SYSTEM_PROMPT = """You are a subagent. A task has been delegated to you by the main agent.

Complete the task using your tools (filesystem, web search, terminal).
When finished, respond with a clear summary of your findings or results.
Do not send messages to the user — your final response is relayed by the main agent."""


class Subagent:
    """Ephemeral worker that runs an isolated LLM loop for one delegated task.

    Lifecycle: created by SubagentManager → runs → announces result via bus →
    discarded.  Has no workspace, sessions, memory, or hooks of its own.
    """

    def __init__(
        self,
        llm: "BaseChatLLM",
        bus: Bus,
        config: "SubagentConfig | None" = None,
        tracer: "Tracer | None" = None,
    ) -> None:
        self.llm = llm
        self.bus = bus
        self.config = config
        self.tracer = tracer

    async def run(self, record: SubagentRecord) -> None:
        """Execute the task and update the record with status/result when done.

        Supports retry with exponential backoff via SubagentConfig.retry.
        """
        logger.info(f"[{record.task_id}] subagent '{record.label}' started")

        # Emit trace event for subagent run start
        if self.tracer:
            from operator_use.tracing import TraceEvent, TraceEventType

            trace_event = TraceEvent(
                span_id=record.task_id,
                event_type=TraceEventType.SUBAGENT_RUN,
                started_at=datetime.now(),
                subagent_task_id=record.task_id,
                subagent_label=record.label,
            )
            # Store the event so we can update it at the end
            self._trace_event = trace_event

        from operator_use.agent.tools.builtin import resolve_tools

        if self.config and self.config.tools:
            tools_cfg = self.config.tools
            tool_list = resolve_tools(
                profile=tools_cfg.profile,
                also_allow=tools_cfg.also_allow,
                deny=tools_cfg.deny,
            )
        else:
            tool_list = FILESYSTEM_TOOLS + WEB_TOOLS + TERMINAL_TOOLS

        registry = ToolRegistry()
        registry.register_tools(tool_list)
        registry.set_extension("_llm", self.llm)

        tools = registry.list_tools()

        system_prompt = (
            self.config.system_prompt
            if (self.config and self.config.system_prompt)
            else DEFAULT_SYSTEM_PROMPT
        )
        max_iterations = self.config.max_iterations if self.config else DEFAULT_MAX_ITERATIONS

        # Retry configuration
        retry_cfg = self.config.retry if self.config else None
        max_retries = retry_cfg.max_retries if retry_cfg else 0
        record.max_retries = max_retries

        result = "(no result)"

        for attempt in range(max_retries + 1):
            # Reset history on each retry attempt
            history = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=record.task),
            ]

            try:
                for _ in range(max_iterations):
                    messages = list(history)

                    event = await self.llm.ainvoke(messages=messages, tools=tools)
                    match event.type:
                        case LLMEventType.TOOL_CALL:
                            tc = event.tool_call
                            tr = await registry.aexecute(tc.name, tc.params)
                            history.append(
                                ToolMessage(
                                    id=tc.id,
                                    name=tc.name,
                                    params=tc.params,
                                    content=tr.output if tr.success else tr.error,
                                    thinking=event.thinking.content if event.thinking else None,
                                    thinking_signature=event.thinking.signature
                                    if event.thinking
                                    else None,
                                )
                            )
                        case LLMEventType.TEXT:
                            result = event.content or "(no result)"
                            break
                else:
                    result = f"(hit {max_iterations}-iteration limit without finishing)"

                record.status = "completed"
                break  # Success — exit retry loop

            except asyncio.CancelledError:
                logger.info(f"[{record.task_id}] subagent '{record.label}' cancelled")
                record.status = "cancelled"
                record.finished_at = datetime.now()
                return

            except Exception as e:
                record.retry_count = attempt
                if attempt < max_retries:
                    # Exponential backoff
                    delay = min(
                        retry_cfg.base_delay * (retry_cfg.backoff_factor**attempt),
                        retry_cfg.max_delay,
                    )
                    logger.warning(
                        f"[{record.task_id}] attempt {attempt + 1} failed: {e}; "
                        f"retrying in {delay:.1f}s (attempt {attempt + 2}/{max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                    # Continue to next retry iteration
                else:
                    # Final failure
                    logger.error(
                        f"[{record.task_id}] subagent '{record.label}' failed after {max_retries + 1} attempts: {e}",
                        exc_info=True,
                    )
                    result = f"(error: {type(e).__name__}: {e})"
                    record.status = "failed"
                    break

        record.result = result
        record.finished_at = datetime.now()
        logger.info(
            f"[{record.task_id}] subagent '{record.label}' done — status={record.status} (attempt {record.retry_count + 1}/{max_retries + 1})"
        )

        # Emit trace event for subagent run end
        if self.tracer and hasattr(self, "_trace_event"):
            from operator_use.tracing import TraceEventType

            # Update the stored trace event with final info
            self._trace_event.finished_at = record.finished_at
            self._trace_event.subagent_status = record.status
            await self.tracer.emit_subagent(self._trace_event)

        await self._announce(record)

    async def _announce(self, record: SubagentRecord) -> None:
        """Publish the result back to the origin session as an incoming message."""
        content = (
            f"[subagent:{record.task_id}] Task '{record.label}' has finished.\n\n"
            f"Result:\n{record.result}\n\n"
            f"Summarize this naturally for the user in 1-2 sentences. "
            f"Do not mention technical terms like 'subagent' or task IDs."
        )
        await self.bus.publish_incoming(
            IncomingMessage(
                channel=record.channel,
                chat_id=record.chat_id,
                account_id=record.account_id,
                parts=[TextPart(content=content)],
                user_id="subagent",
                metadata={"_subagent_result": True, "task_id": record.task_id},
            )
        )
