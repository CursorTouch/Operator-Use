"""Observability trace data structures."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TraceEventType(str, Enum):
    """Type of operation being traced."""

    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    SUBAGENT_RUN = "subagent_run"
    AGENT_RUN = "agent_run"


@dataclass
class TraceEvent:
    """A single trace event with timing, token, and operation details."""

    span_id: str  # Unique ID for this span
    event_type: TraceEventType
    started_at: datetime
    agent_id: str = ""
    parent_span_id: str | None = None  # Parent agent or operation span
    finished_at: datetime | None = None  # Set when operation completes
    duration_ms: float | None = None  # Calculated from started_at and finished_at

    # Token accounting (from LLM usage)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    # Tool-specific
    tool_name: str | None = None
    tool_result_preview: str | None = None  # First 200 chars of result
    tool_success: bool | None = None

    # Subagent-specific
    subagent_task_id: str | None = None
    subagent_label: str | None = None
    subagent_status: str | None = None  # "running", "completed", "failed", "cancelled"

    # Error info
    error: str | None = None

    def __post_init__(self) -> None:
        """Compute duration_ms if both timestamps are set."""
        self._update_duration()

    def _update_duration(self) -> None:
        """Recalculate duration_ms from timestamps."""
        if self.finished_at and not self.duration_ms:
            self.duration_ms = (self.finished_at - self.started_at).total_seconds() * 1000
