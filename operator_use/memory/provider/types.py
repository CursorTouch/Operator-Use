from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions


@dataclass
class MemoryProvider:
    id: str
    name: str
    api: str | Type[BaseMemoryAPI]
    options: MemoryOptions
