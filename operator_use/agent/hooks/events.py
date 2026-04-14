"""Hook event types and context dataclasses."""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operator_use.bus import IncomingMessage
    from operator_use.messages import AIMessage, BaseMessage
    from operator_use.session import Session
    from operator_use.providers.events import LLMToolCall, LLMEvent
    from operator_use.agent.tools import ToolResult


class HookEvent(str, Enum):
    BEFORE_AGENT_START = "before_agent_start"
    AFTER_AGENT_START = "after_agent_start"
    BEFORE_AGENT_END = "before_agent_end"
    AFTER_AGENT_END = "after_agent_end"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_CALL = "after_llm_call"


@dataclass
class BeforeAgentStartContext:
    """Fired just before the agentic loop begins."""

    message: "IncomingMessage | None"
    session: "Session"


@dataclass
class AfterAgentStartContext:
    """Fired after the first LLM call is dispatched."""

    message: "IncomingMessage | None"
    session: "Session"
    iteration: int


@dataclass
class BeforeAgentEndContext:
    """Fired when the loop produces a final AIMessage, before session save.

    Modify ``response.content`` to override what gets sent.
    """

    message: "IncomingMessage | None"
    session: "Session"
    response: "AIMessage"


@dataclass
class AfterAgentEndContext:
    """Fired after session is saved and the final AIMessage is ready."""

    message: "IncomingMessage | None"
    session: "Session"
    response: "AIMessage"


@dataclass
class BeforeToolCallContext:
    """Fired before a tool is executed.

    Set ``skip=True`` and populate ``result`` to short-circuit execution.
    """

    session: "Session"
    tool_call: "LLMToolCall"
    skip: bool = False
    result: Any = None


@dataclass
class AfterToolCallContext:
    """Fired after a tool executes (success or failure)."""

    session: "Session"
    tool_call: "LLMToolCall"
    tool_result: "ToolResult"
    content: Any


@dataclass
class BeforeLLMCallContext:
    """Fired just before each LLM invocation in the agentic loop.

    Handlers may mutate ``messages`` to modify what the LLM receives
    (e.g. context compression, injection, filtering).
    """

    session: "Session"
    messages: "list[BaseMessage]"
    iteration: int


@dataclass
class AfterLLMCallContext:
    """Fired immediately after each LLM invocation returns.

    Handlers may inspect or mutate ``event`` to override the LLM response.
    """

    session: "Session"
    messages: "list[BaseMessage]"
    event: "LLMEvent"
    iteration: int
