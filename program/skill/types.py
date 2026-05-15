from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.resource.loader import SourceInfo


@dataclass
class Skill:
    name: str
    description: str
    file_path: str
    base_dir: str
    source_info: Optional[SourceInfo] = None
    disable_model_invocation: bool = False


@dataclass
class LoadSkillsResult:
    skills: list[Skill] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
