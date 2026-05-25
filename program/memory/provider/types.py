from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from program.memory.api.base import BaseMemoryAPI
from program.memory.types import MemoryOptions


@dataclass
class MemoryProvider:
    id: str
    name: str
    api: str | Type[BaseMemoryAPI]
    options: MemoryOptions
