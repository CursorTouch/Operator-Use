"""AgentLoop: shared LLM+tool iteration logic for all non-streaming loops.

Used by the main Agent (_loop), browser_task, computer_task, and Subagent.
The streaming loop (_loop_stream) stays separate because it uses astream().
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from operator_use.messages import AIMessage, HumanMessage, ToolMessage
from operator_use.providers.events import LLMEventType

if TYPE_CHECKING:
    from operator_use.agent.hooks import Hooks
    from operator_use.agent.tools import ToolRegistry
    from operator_use.providers.base import BaseChatLLM
    from operator_use.session import Session
    from operator_use.web.loop import LoopGuard

logger = logging.getLogger(__name__)

BeforeCallFn = Callable[[list, int], Awaitable[list]]
AfterToolFn = Callable[..., Awaitable[None]]
BuildMessagesFn = Callable[[int], Awaitable[list]]


class AgentLoop:
    """LLM+tool loop for non-streaming agentic tasks.

    Two modes:

    **Simple** (browser_task, computer_task, Subagent) — pass a flat *history*
    list to ``run()``. The loop appends ToolMessages in-place and returns when
    the LLM produces text or an error.

    **Full** (main Agent) — pass *hooks*, *session*, *build_messages*, and
    *accumulate_errors*. Messages are rebuilt from the session each iteration,
    all hook events fire, and failed tool calls are fed back to the LLM on
    the next iteration.

    Args:
        llm: LLM provider.
        registry: Tool registry for this loop.
        max_iterations: Hard cap on LLM calls.
        name: Label used in log lines (e.g. ``"browser_task"``, agent id).
        before_call: async (messages, iteration) → messages.
            Inject state (browser/desktop) before each LLM call.
        after_tool: async (tool_call, tr) → None.
            Called after each tool execution (e.g. browser page recording).
        loop_guard: Optional LoopGuard for repetition/stagnation detection.
        hooks: Hooks object — fires BEFORE/AFTER_LLM_CALL and
            BEFORE/AFTER_TOOL_CALL events (main Agent only).
        session: Session — messages are persisted here and used as source of
            truth for history when *build_messages* is provided.
        build_messages: async (iteration) → list.
            When provided, called each iteration to build the full message list
            (system prompt + hydrated history). Replaces the flat *history*
            argument of ``run()``.
        accumulate_errors: When True, failed ToolMessages are held in a side
            queue and appended to the next LLM call so the model can self-correct.
            Cleared on the first successful tool call.
        incoming_message: IncomingMessage passed to AFTER_AGENT_START hook on
            the first iteration (main Agent only).
    """

    def __init__(
        self,
        llm: "BaseChatLLM",
        registry: "ToolRegistry",
        max_iterations: int = 30,
        name: str = "loop",
        before_call: "BeforeCallFn | None" = None,
        after_tool: "AfterToolFn | None" = None,
        loop_guard: "LoopGuard | None" = None,
        hooks: "Hooks | None" = None,
        accumulate_errors: bool = False,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.max_iterations = max_iterations
        self.name = name
        self.before_call = before_call
        self.after_tool = after_tool
        self.loop_guard = loop_guard
        self.hooks = hooks
        self.accumulate_errors = accumulate_errors
        self._tools = registry.list_tools()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        history: list | None = None,
        session: "Session | None" = None,
        incoming_message=None,
        build_messages: "BuildMessagesFn | None" = None,
    ) -> AIMessage:
        """Run the loop.

        Args:
            history: Flat message list mutated in-place with ToolMessages.
                     Required in simple mode (no *build_messages*).
            session: Per-call session for message persistence and hooks context.
            incoming_message: IncomingMessage for the AFTER_AGENT_START hook
                              (main Agent only, first iteration).
            build_messages: async (iteration) → list. When provided, called
                            each iteration to rebuild the full message list from
                            the session. Replaces *history* in full mode.

        Returns:
            AIMessage with the final LLM text response.
        Raises:
            RuntimeError: On LLM error events (simple mode) or exceeded
                          max_iterations (full mode with session).
        """
        from operator_use.agent.hooks import HookEvent
        from operator_use.agent.hooks.events import (
            AfterAgentStartContext,
            AfterLLMCallContext,
            AfterToolCallContext,
            BeforeLLMCallContext,
            BeforeToolCallContext,
        )

        error_messages: list[ToolMessage] = []

        for iteration in range(self.max_iterations):
            # Build the message list for this iteration
            if build_messages is not None:
                messages = await build_messages(iteration)
            else:
                messages = list(history)  # type: ignore[arg-type]

            # Append accumulated error messages so the LLM can self-correct
            if error_messages:
                messages.extend(error_messages)

            # State injection (browser / desktop state)
            if self.before_call is not None:
                messages = await self.before_call(messages, iteration)

            # Loop-guard warning injection
            if self.loop_guard is not None:
                warning = self.loop_guard.check()
                if warning:
                    messages.append(HumanMessage(content=f"[LoopGuard] {warning}"))

            # AFTER_AGENT_START — first iteration only
            if iteration == 0 and self.hooks and incoming_message:
                await self.hooks.emit(
                    HookEvent.AFTER_AGENT_START,
                    AfterAgentStartContext(
                        message=incoming_message,
                        session=session,
                        iteration=0,
                    ),
                )

            # BEFORE_LLM_CALL
            if self.hooks:
                before_ctx = await self.hooks.emit(
                    HookEvent.BEFORE_LLM_CALL,
                    BeforeLLMCallContext(
                        session=session, messages=messages, iteration=iteration
                    ),
                )
                messages = before_ctx.messages

            logger.info(
                "[%s] LLM call | iter=%d messages=%d tools=%d",
                self.name, iteration, len(messages), len(self._tools),
            )

            event = await self.llm.ainvoke(messages=messages, tools=self._tools)

            # AFTER_LLM_CALL
            if self.hooks:
                after_ctx = await self.hooks.emit(
                    HookEvent.AFTER_LLM_CALL,
                    AfterLLMCallContext(
                        session=session,
                        messages=messages,
                        event=event,
                        iteration=iteration,
                    ),
                )
                event = after_ctx.event

            thinking = event.thinking.content if event.thinking else None
            thinking_signature = event.thinking.signature if event.thinking else None

            match event.type:
                case LLMEventType.TOOL_CALL:
                    tool_call = event.tool_call

                    # BEFORE_TOOL_CALL (with skip/override support)
                    tool_result = None
                    if self.hooks:
                        pre_ctx = await self.hooks.emit(
                            HookEvent.BEFORE_TOOL_CALL,
                            BeforeToolCallContext(session=session, tool_call=tool_call),
                        )
                        if pre_ctx.skip:
                            tool_result = pre_ctx.result

                    if tool_result is None:
                        tool_result = await self.registry.aexecute(tool_call.name, tool_call.params)

                    content = tool_result.output if tool_result.success else tool_result.error

                    if tool_result.success:
                        logger.info("[%s] tool=%s -> %s", self.name, tool_call.name, (content or "")[:200])
                    else:
                        logger.warning("[%s] tool=%s ERROR: %s", self.name, tool_call.name, content)

                    # AFTER_TOOL_CALL
                    if self.hooks:
                        await self.hooks.emit(
                            HookEvent.AFTER_TOOL_CALL,
                            AfterToolCallContext(
                                session=session,
                                tool_call=tool_call,
                                tool_result=tool_result,
                                content=content,
                            ),
                        )

                    if self.loop_guard is not None:
                        self.loop_guard.record_action(tool_call.name, tool_call.params, tool_result.success)

                    if self.after_tool is not None:
                        await self.after_tool(tool_call, tool_result)

                    tool_message = ToolMessage(
                        id=tool_call.id,
                        name=tool_call.name,
                        params=tool_call.params,
                        content=content,
                        thinking=thinking,
                        thinking_signature=thinking_signature,
                    )

                    if self.accumulate_errors:
                        if tool_result.success:
                            self._persist(tool_message, history, session)
                            error_messages.clear()
                        else:
                            error_messages.append(tool_message)
                    else:
                        self._persist(tool_message, history, session)

                    if tool_result.metadata and tool_result.metadata.get("stop_loop"):
                        return AIMessage(content=content or "")

                case LLMEventType.TEXT:
                    text = event.content or ""
                    if session:
                        text = self._clean_content(text)
                    else:
                        text = text or "(no result)"
                    logger.info(
                        "[%s] response | %r%s",
                        self.name, text[:120], "..." if len(text) > 120 else "",
                    )
                    msg = AIMessage(
                        content=text, thinking=thinking, thinking_signature=thinking_signature
                    )
                    if session:
                        session.add_message(msg)
                    return msg

                case LLMEventType.ERROR:
                    error_text = event.error or "Unknown LLM error"
                    logger.error("[%s] LLM error | %s", self.name, error_text)
                    if session:
                        msg = AIMessage(content=f"Sorry, I encountered an error: {error_text}")
                        session.add_message(msg)
                        return msg
                    raise RuntimeError(error_text)

        # Max iterations
        if session:
            raise RuntimeError(f"Agent exceeded max_iterations ({self.max_iterations})")
        return AIMessage(content=f"(hit {self.max_iterations}-iteration limit without finishing)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _persist(self, msg: ToolMessage, history: list | None, session: "Session | None") -> None:
        """Add a tool message to the history list and/or session."""
        if history is not None:
            history.append(msg)
        if session is not None:
            session.add_message(msg)

    @staticmethod
    def _clean_content(content: str) -> str:
        """Strip <think> blocks, leading message IDs, and control tags."""
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
        content = re.sub(r"^\[(bot_)?msg_id:\d+\]\s*", "", content)
        content = re.sub(r"<ctrl\d+>", "", content)
        return content.strip() or "(no response)"
