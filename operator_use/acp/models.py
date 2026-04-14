"""ACP (Agent Communication Protocol) Pydantic models.

Implements the BeeAI ACP spec:
  https://agentcommunicationprotocol.dev/

REST endpoints:
  GET  /agents              -> list agents
  GET  /agents/{agent_id}   -> agent metadata
  POST /runs                -> create run
  GET  /runs/{run_id}       -> run status
  DELETE /runs/{run_id}     -> cancel run
  GET  /runs/{run_id}/await -> SSE stream (run completion / output chunks)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Message parts (mirrors OpenAI content-part convention used by ACP spec)
# ---------------------------------------------------------------------------


class TextMessagePart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageURLMessagePart(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: dict[str, str]  # {"url": "data:image/jpeg;base64,..."}


class FileMessagePart(BaseModel):
    type: Literal["file"] = "file"
    file: dict[str, str]  # {"name": "...", "content": "<base64>", "mime_type": "..."}


MessagePart = TextMessagePart | ImageURLMessagePart | FileMessagePart


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    AWAITING = "awaiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMode(str, Enum):
    SYNC = "sync"  # Wait for completion, return full output
    ASYNC = "async"  # Return run immediately, poll or SSE for result
    STREAM = "stream"  # SSE stream output chunks as they arrive


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    """POST /runs body."""

    agent_id: str = "operator"
    session_id: str | None = None
    mode: RunMode = RunMode.ASYNC
    input: list[MessagePart] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """Canonical run object returned by the API."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str = "operator"
    session_id: str | None = None
    status: RunStatus = RunStatus.CREATED
    mode: RunMode = RunMode.ASYNC
    input: list[MessagePart] = Field(default_factory=list)
    output: list[MessagePart] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None


# ---------------------------------------------------------------------------
# Agent descriptor
# ---------------------------------------------------------------------------


class AgentCapabilities(BaseModel):
    streaming: bool = True
    async_mode: bool = True
    session: bool = True


class AgentMetadata(BaseModel):
    """GET /agents/{agent_id} response."""

    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentListResponse(BaseModel):
    id: str = ""  # Stable UUID identifying this ACP server instance
    agents: list[AgentMetadata]


# ---------------------------------------------------------------------------
# SSE event shapes
# ---------------------------------------------------------------------------


class RunOutputEvent(BaseModel):
    """SSE event: output chunk or completed signal."""

    type: Literal["output", "completed", "error"] = "output"
    run_id: str
    part: MessagePart | None = None  # present when type == "output"
    error: str | None = None  # present when type == "error"
