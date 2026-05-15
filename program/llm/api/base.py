from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from program.llm.types import LLMContext, LLMEvent, Options, Transport
from program.message.types import BaseMessage


class BaseAPI(ABC):
    SUPPORTED_TRANSPORTS: tuple[Transport, ...] = (Transport.HTTP,)

    def __init__(self, options: Options) -> None:
        if options.transport not in self.SUPPORTED_TRANSPORTS:
            raise ValueError(
                f"{self.__class__.__name__} does not support transport '{options.transport.value}'. "
                f"Supported: {[t.value for t in self.SUPPORTED_TRANSPORTS]}"
            )
        self.options = options

    def _cancelled(self) -> bool:
        return self.options.signal is not None and self.options.signal.is_set()

    @abstractmethod
    def stream(self, context: LLMContext, model: str) -> AsyncIterator[LLMEvent]: ...

    @abstractmethod
    async def invoke(self, context: LLMContext, model: str) -> list[LLMEvent]: ...
