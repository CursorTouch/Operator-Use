from __future__ import annotations

from dataclasses import dataclass, field

from program.llm.model.types import Cost, Modality


@dataclass
class Model:
    id: str
    name: str
    provider: str
    cost: Cost = field(default_factory=Cost)
    input: list[Modality] = field(default_factory=list)
    output: list[Modality] = field(default_factory=list)
