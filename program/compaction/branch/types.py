from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict

if TYPE_CHECKING:
    from program.inference.api.text.service import LLM
    from program.session.types import SessionEntry

from program.message.types import AgentMessage
from program.compaction.strategy.types import FileOperations


class BranchSummaryDetails(BaseModel):
    read_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)


class BranchSummaryResult(BaseModel):
    summary: str | None = None
    read_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    aborted: bool = False
    error: str | None = None


class BranchPreparation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[AgentMessage]
    file_ops: FileOperations
    total_tokens: int


class CollectEntriesResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    entries: list[SessionEntry]
    common_ancestor_id: str | None = None


class GenerateBranchSummaryOptions(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm: LLM
    custom_instructions: str | None = None
    replace_instructions: bool = False
    reserve_tokens: int = 16384
    context_window: int = 128000
