from dataclasses import dataclass
from typing import Type
from program.llm.api.base import BaseAPI
from program.llm.types import Options


@dataclass
class Provider:
    name: str
    api: Type[BaseAPI]
    options: Options
