from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ============================================================================
# Source Info
# ============================================================================

class SourceInfo(BaseModel):
    path: str
    source: str                  # "user", "project", "path", or custom
    scope: str | None = None     # "user" | "project" | None
    base_dir: str | None = None


# ============================================================================
# Diagnostics
# ============================================================================

class CollisionInfo(BaseModel):
    resource_type: str           # "skill"
    name: str
    winner_path: str
    loser_path: str


class ResourceDiagnostic(BaseModel):
    type: Literal["warning", "collision"]
    message: str
    path: str
    collision: CollisionInfo | None = None


# ============================================================================
# Skill
# ============================================================================

class SkillFrontmatter(BaseModel):
    name: str | None = None
    description: str | None = None
    disable_model_invocation: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    name: str
    description: str
    file_path: Path
    base_dir: Path
    source_info: SourceInfo
    disable_model_invocation: bool = False


# ============================================================================
# Load results
# ============================================================================

class LoadSkillsResult(BaseModel):
    skills: list[Skill] = Field(default_factory=list)
    diagnostics: list[ResourceDiagnostic] = Field(default_factory=list)


class LoadSkillsOptions(BaseModel):
    cwd: Path
    agent_dir: Path
    skill_paths: list[str] = Field(default_factory=list)
    include_defaults: bool = True
