from dataclasses import dataclass
from typing import Type
from program.llm.api.base import BaseAPI
from program.llm.types import AuthType, Options

__all__ = ["AuthType", "Provider"]


@dataclass
class Provider:
    name: str
    api: Type[BaseAPI]
    options: Options

    def get_api_key(self) -> str | None:
        return self.options.api_key

    def get_base_url(self) -> str | None:
        return self.options.base_url
