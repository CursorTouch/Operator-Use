from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from program.llm.types import LLMEvent, Options
from program.message.types import BaseMessage


class BaseAPI(ABC):
    def __init__(self, options: Options) -> None:
        self.options = options

    def _cancelled(self) -> bool:
        return self.options.signal is not None and self.options.signal.is_set()

    @abstractmethod
    def stream(
        self, messages: list[BaseMessage], model: str
    ) -> AsyncIterator[LLMEvent]: ...

    @abstractmethod
    async def invoke(
        self, messages: list[BaseMessage], model: str
    ) -> list[LLMEvent]: ...
