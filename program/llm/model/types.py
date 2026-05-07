from dataclasses import dataclass, field
from enum import Enum


class Modality(str, Enum):
    Text = "text"
    Image = "image"


@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class Model:
    id: str
    name: str
    provider: str
    cost: Cost = field(default_factory=Cost)
    thinking: bool = False
    context_window: int = 0
    input: list[Modality] = field(default_factory=list)
    output: list[Modality] = field(default_factory=list)