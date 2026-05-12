from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from program.llm.types import LLMEvent, Options, TransportType
from program.message.types import BaseMessage
from typing import TYPE_CHECKING, Optional
from program.tool.types import Tool


class BaseAPI(ABC):
    SUPPORTED_TRANSPORTS: tuple[TransportType, ...] = (TransportType.HTTP,)

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
    def stream(
        self, messages: list[BaseMessage], model: str, tools: Optional[list[Tool]] = None
    ) -> AsyncIterator[LLMEvent]: ...

    @abstractmethod
    async def invoke(
        self, messages: list[BaseMessage], model: str, tools: Optional[list[Tool]] = None
    ) -> list[LLMEvent]: ...
