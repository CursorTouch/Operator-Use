from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from program.llm.types import LLMEvent, Options
from program.message.types import BaseMessage


class BaseAPI(ABC):
    def __init__(self, options: Options) -> None:
        self.options = options

    @abstractmethod
    def stream(
        self, messages: list[BaseMessage], model: str
    ) -> AsyncIterator[LLMEvent]: ...

    @abstractmethod
    async def invoke(
        self, messages: list[BaseMessage], model: str
    ) -> list[LLMEvent]: ...
