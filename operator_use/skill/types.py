from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# CollisionInfo and ResourceDiagnostic are defined in the diagnostics module
# and re-exported here so existing imports keep working.
from operator_use.diagnostics.types import CollisionInfo, ResourceDiagnostic


# ============================================================================
# Source Info
# ============================================================================

class SourceInfo(BaseModel):
    path: str
    source: str                  # "user", "project", "path", or custom
    scope: str | None = None     # "user" | "project" | None
    base_dir: str | None = None


# ============================================================================
# Skill
# ============================================================================

class SkillFrontmatter(BaseModel):
    name: str | None = None
    description: str | None = None
    disable_model_invocation: bool = False
    requires_tools: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    name: str
    description: str
    file_path: Path
    base_dir: Path
    source_info: SourceInfo
    disable_model_invocation: bool = False
    requires_tools: list[str] = Field(default_factory=list)


# ============================================================================
# Load results
# ============================================================================

class LoadSkillsResult(BaseModel):
    skills: list[Skill] = Field(default_factory=list)
    diagnostics: list[ResourceDiagnostic] = Field(default_factory=list)


class LoadSkillsOptions(BaseModel):
    cwd: Path
    skill_paths: list[str] = Field(default_factory=list)
    include_defaults: bool = True
