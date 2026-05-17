from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from program.inference.model.types import Model
from program.inference.types import LLMContext, LLMEvent, LLMOptions, Transport
from program.message.types import BaseMessage


class BaseLLMAPI(ABC):
    SUPPORTED_TRANSPORTS: tuple[Transport, ...] = (Transport.HTTP,)

    def __init__(self, options: LLMOptions) -> None:
        if options.transport not in self.SUPPORTED_TRANSPORTS:
            raise ValueError(
                f"{self.__class__.__name__} does not support transport '{options.transport.value}'. "
                f"Supported: {[t.value for t in self.SUPPORTED_TRANSPORTS]}"
            )
        self.options = options

    def _cancelled(self) -> bool:
        return self.options.signal is not None and self.options.signal.is_set()

    @abstractmethod
    def stream(self, context: LLMContext, model: Model) -> AsyncIterator[LLMEvent]: ...

    @abstractmethod
    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]: ...


# Backward-compat alias (existing code imports BaseAPI from llm.api.base)
BaseAPI = BaseLLMAPI
