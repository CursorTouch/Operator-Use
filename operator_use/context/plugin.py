"""ContextPlugin: attaches a context strategy to the agent's BEFORE_LLM_CALL hook."""

import logging
from typing import TYPE_CHECKING

from operator_use.plugins.base import Plugin
from operator_use.context.base import BaseContextStrategy

if TYPE_CHECKING:
    from operator_use.agent.hooks import Hooks
    from operator_use.agent.hooks.events import BeforeLLMCallContext
    from operator_use.providers.base import BaseChatLLM

logger = logging.getLogger(__name__)


class ContextPlugin(Plugin):
    """Applies a pluggable context management strategy before each LLM call.

    Usage::

        from operator_use.context import ContextPlugin
        from operator_use.context.strategies import CompactionStrategy

        plugin = ContextPlugin(strategy=CompactionStrategy(threshold=20), llm=llm)

    The strategy's ``process()`` method receives the full message list and may
    return a compressed, trimmed, or otherwise modified version of it.
    """

    name = "context"

    def __init__(self, strategy: BaseContextStrategy, llm: "BaseChatLLM | None" = None):
        self._strategy = strategy
        self._llm = llm

    def register_hooks(self, hooks: "Hooks") -> None:
        from operator_use.agent.hooks import HookEvent

        hooks.register(HookEvent.BEFORE_LLM_CALL, self._hook)

    def unregister_hooks(self, hooks: "Hooks") -> None:
        from operator_use.agent.hooks import HookEvent

        hooks.unregister(HookEvent.BEFORE_LLM_CALL, self._hook)

    async def _hook(self, ctx: "BeforeLLMCallContext") -> None:
        ctx.messages = await self._strategy.process(ctx.messages, self._llm)
