from __future__ import annotations

from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict

if TYPE_CHECKING:
    from program.inference.api.text.service import LLM
    from program.session.types import SessionEntry

from program.message.types import AgentMessage


class FileOperations(BaseModel):
    read: set[str] = Field(default_factory=set)
    written: set[str] = Field(default_factory=set)
    edited: set[str] = Field(default_factory=set)


class CompactionDetails(BaseModel):
    read_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)


class CompactionSettings(BaseModel):
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


class ContextUsageEstimate(BaseModel):
    tokens: int = 0
    usage_tokens: int = 0
    trailing_tokens: int = 0
    last_usage_index: int | None = None


class CutPointResult(BaseModel):
    first_kept_entry_index: int = 0
    turn_start_index: int = -1
    is_split_turn: bool = False


class CompactionResult(BaseModel):
    summary: str
    retained_from_id: str
    tokens_before: int
    details: Any | None = None


class CompactionPreparation(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    retained_from_id: str
    messages_to_summarize: list[AgentMessage]
    turn_prefix_messages: list[AgentMessage]
    is_split_turn: bool
    tokens_before: int
    previous_summary: str | None
    file_ops: FileOperations
    settings: CompactionSettings


# ============================================================================
# Branch summarization types
# ============================================================================

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

    entries: list["SessionEntry"]
    common_ancestor_id: str | None = None


class GenerateBranchSummaryOptions(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm: "LLM"
    custom_instructions: str | None = None
    replace_instructions: bool = False
    reserve_tokens: int = 16384
    context_window: int = 128000
