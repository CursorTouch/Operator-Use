from __future__ import annotations
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.llm.service import LLM
    from program.tool.types import Tool

from program.agent.types import AgentState, Options, AgentEvent
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
        )