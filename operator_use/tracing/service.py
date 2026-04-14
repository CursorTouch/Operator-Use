"""Tracer — unified observability system that hooks into agent execution."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from operator_use.tracing.views import TraceEvent, TraceEventType

if TYPE_CHECKING:
    from operator_use.agent.hooks import Hooks

logger = logging.getLogger(__name__)


class Tracer:
    """Observability tracer that emits TraceEvent objects for all agent and tool execution.

    Registers hook handlers on an Agent's Hooks system to capture timing, tokens, and
    operation details. Can emit events to a sync or async callback.

    Usage:
        async def on_trace(event: TraceEvent):
            print(event)

        tracer = Tracer(agent_id="main", on_trace=on_trace)
        agent = Agent(..., tracer=tracer)
    """

    def __init__(self, agent_id: str, on_trace: Callable[[TraceEvent], Any]) -> None:
        """Initialize tracer with agent ID and callback.

        Args:
            agent_id: Identifier for the agent being traced
            on_trace: Sync or async callable that receives TraceEvent objects
        """
        self.agent_id = agent_id
        self._on_trace = on_trace
        self._pending: dict[str, dict[str, Any]] = {}  # span_id -> partial event data
        self._current_agent_span: str | None = None

    def register(self, hooks: "Hooks") -> None:
        """Register all trace handlers on the given Hooks instance."""
        from operator_use.agent.hooks import HookEvent

        hooks.register(HookEvent.BEFORE_AGENT_START, self._before_agent_start)
        hooks.register(HookEvent.AFTER_AGENT_END, self._after_agent_end)
        hooks.register(HookEvent.BEFORE_LLM_CALL, self._before_llm_call)
        hooks.register(HookEvent.AFTER_LLM_CALL, self._after_llm_call)
        hooks.register(HookEvent.BEFORE_TOOL_CALL, self._before_tool_call)
        hooks.register(HookEvent.AFTER_TOOL_CALL, self._after_tool_call)

    async def emit_subagent(self, event: TraceEvent) -> None:
        """Emit a subagent trace event. Called directly by Subagent.run()."""
        await self._emit(event)

    async def _emit(self, event: TraceEvent) -> None:
        """Emit a trace event to the callback (handles sync or async callbacks)."""
        try:
            if asyncio.iscoroutinefunction(self._on_trace):
                await self._on_trace(event)
            else:
                self._on_trace(event)
        except Exception as e:
            logger.error(f"Error emitting trace event: {e}", exc_info=True)

    # Hook handlers

    async def _before_agent_start(self, context: Any) -> None:
        """Called when an agent run starts."""
        self._current_agent_span = f"agent_{uuid.uuid4().hex[:8]}"
        self._pending[self._current_agent_span] = {
            "event_type": TraceEventType.AGENT_RUN,
            "started_at": datetime.now(),
        }

    async def _after_agent_end(self, context: Any) -> None:
        """Called after an agent run completes."""
        if not self._current_agent_span:
            return

        span_id = self._current_agent_span
        if span_id in self._pending:
            data = self._pending.pop(span_id)
            event = TraceEvent(
                span_id=span_id,
                event_type=data["event_type"],
                started_at=data["started_at"],
                finished_at=datetime.now(),
                agent_id=self.agent_id,
            )
            await self._emit(event)

        self._current_agent_span = None

    async def _before_llm_call(self, context: Any) -> None:
        """Called before an LLM call."""
        span_id = f"llm_{uuid.uuid4().hex[:8]}"
        self._pending[span_id] = {
            "event_type": TraceEventType.LLM_CALL,
            "started_at": datetime.now(),
        }
        # Attach to hook context so _after_llm_call can find it
        if hasattr(context, "_span_id"):
            context._span_id = span_id
        else:
            # Store mapping in a context attribute (hook contexts may not support this)
            # Fallback: store in pending and pop by event_type
            pass

    async def _after_llm_call(self, context: Any) -> None:
        """Called after an LLM call. Extracts token usage from event.usage."""
        # Find the LLM span we just created (most recent pending LLM_CALL)
        llm_spans = [
            (sid, data)
            for sid, data in self._pending.items()
            if data.get("event_type") == TraceEventType.LLM_CALL and "finished_at" not in data
        ]
        if not llm_spans:
            return

        span_id, data = llm_spans[-1]  # Take the most recent
        self._pending.pop(span_id, None)

        # Extract token usage from LLM event
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None

        if hasattr(context, "event") and context.event and hasattr(context.event, "usage"):
            usage = context.event.usage
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", None)
                completion_tokens = getattr(usage, "completion_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)

        event = TraceEvent(
            span_id=span_id,
            event_type=TraceEventType.LLM_CALL,
            started_at=data["started_at"],
            finished_at=datetime.now(),
            agent_id=self.agent_id,
            parent_span_id=self._current_agent_span,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        await self._emit(event)

    async def _before_tool_call(self, context: Any) -> None:
        """Called before a tool is executed."""
        span_id = f"tool_{uuid.uuid4().hex[:8]}"
        self._pending[span_id] = {
            "event_type": TraceEventType.TOOL_CALL,
            "started_at": datetime.now(),
        }

    async def _after_tool_call(self, context: Any) -> None:
        """Called after a tool execution. Extracts tool name and result."""
        # Find the tool span we just created
        tool_spans = [
            (sid, data)
            for sid, data in self._pending.items()
            if data.get("event_type") == TraceEventType.TOOL_CALL and "finished_at" not in data
        ]
        if not tool_spans:
            return

        span_id, data = tool_spans[-1]  # Take the most recent
        self._pending.pop(span_id, None)

        # Extract tool info from context
        tool_name = None
        tool_result_preview = None
        tool_success = None

        if hasattr(context, "tool_call") and context.tool_call:
            tool_name = getattr(context.tool_call, "name", None)

        if hasattr(context, "tool_result") and context.tool_result:
            tool_success = getattr(context.tool_result, "success", None)
            result_output = getattr(context.tool_result, "output", None)
            if result_output:
                tool_result_preview = str(result_output)[:200]

        event = TraceEvent(
            span_id=span_id,
            event_type=TraceEventType.TOOL_CALL,
            started_at=data["started_at"],
            finished_at=datetime.now(),
            agent_id=self.agent_id,
            parent_span_id=self._current_agent_span,
            tool_name=tool_name,
            tool_result_preview=tool_result_preview,
            tool_success=tool_success,
        )
        await self._emit(event)
