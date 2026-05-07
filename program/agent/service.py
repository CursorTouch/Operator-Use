from __future__ import annotations
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.llm.service import LLM
    from program.tool.types import Tool

from program.agent.types import (
    AgentState,
    Options,
    AgentEvent,
    FollowupQueue,
    SteeringQueue,
)
from program.message.types import BaseMessage


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        system_prompt: Optional[str] = None,
        options: Optional[Options] = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.options = options or Options()
        self.state = AgentState(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            follow_up_queue=FollowupQueue(mode=self.options.followup_mode),
            steering_queue=SteeringQueue(mode=self.options.steering_mode),
        )

    async def steer(self, message: BaseMessage) -> None:
        """Add a steering message to the steering queue."""
        await self.state.steering_queue.add(message)

    async def follow_up(self, message: BaseMessage) -> None:
        """Add a follow-up message to the follow-up queue."""
        await self.state.follow_up_queue.add(message)

    def clear_steering(self) -> None:
        """Clear all messages from the steering queue."""
        self.state.steering_queue.clear()

    def clear_follow_up(self) -> None:
        """Clear all messages from the follow-up queue."""
        self.state.follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        """Clear all messages from the steering and follow-up queues."""
        self.state.steering_queue.clear()
        self.state.follow_up_queue.clear()

    def has_pending_messages(self) -> bool:
        """Check if there are any pending messages."""
        return not self.state.steering_queue.is_empty() or not self.state.follow_up_queue.is_empty()

    def reset(self) -> None:
        """Reset agent state: clear queues, error message, tool calls, and streaming flag."""
        self.state.follow_up_queue.clear()
        self.state.steering_queue.clear()
        self.state.error_message = None
        self.state.pending_tool_calls.clear()
        self.state.is_streaming = False
