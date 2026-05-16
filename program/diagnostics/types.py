from __future__ import annotations

from typing import Literal
from pydantic import BaseModel


class CollisionInfo(BaseModel):
    resource_type: str           # "skill" | "tool" | "command"
    name: str
    winner_path: str
    loser_path: str


class ResourceDiagnostic(BaseModel):
    type: Literal["warning", "collision", "error"]
    message: str
    path: str
    collision: CollisionInfo | None = None
