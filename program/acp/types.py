from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    CREATED     = 'created'
    IN_PROGRESS = 'in_progress'
    AWAITING    = 'awaiting'
    COMPLETED   = 'completed'
    FAILED      = 'failed'
    CANCELLED   = 'cancelled'


class RunMode(str, Enum):
    SYNC   = 'sync'    # wait for full output before returning
    ASYNC  = 'async'   # return immediately, poll /runs/{id}
    STREAM = 'stream'  # return immediately, stream via SSE or WebSocket


# ── Message parts ──────────────────────────────────────────────────────────────

class TextMessagePart(BaseModel):
    model_config = ConfigDict(extra='ignore')
    type: Literal['text'] = 'text'
    text: str = ''


class ImageURLContent(BaseModel):
    model_config = ConfigDict(extra='ignore')
    url: str = ''  # data URI or remote URL


class ImageURLMessagePart(BaseModel):
    model_config = ConfigDict(extra='ignore')
    type: Literal['image_url'] = 'image_url'
    image_url: ImageURLContent = ImageURLContent()


class FileContent(BaseModel):
    model_config = ConfigDict(extra='ignore')
    name: str = ''
    content: str = ''       # base64-encoded
    mime_type: str = ''


class FileMessagePart(BaseModel):
    model_config = ConfigDict(extra='ignore')
    type: Literal['file'] = 'file'
    file: FileContent = FileContent()


MessagePart = Annotated[
    TextMessagePart | ImageURLMessagePart | FileMessagePart,
    Field(discriminator='type'),
]


# ── Agent metadata ─────────────────────────────────────────────────────────────

class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra='ignore')
    streaming:  bool = True
    async_mode: bool = True
    sessions:   bool = True


class AgentMetadata(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id:           str = ''
    name:         str = ''
    description:  str = ''
    version:      str = '1.0.0'
    capabilities: AgentCapabilities = AgentCapabilities()
    metadata:     dict[str, Any] = Field(default_factory=dict)


class AgentListResponse(BaseModel):
    model_config = ConfigDict(extra='ignore')
    agents:      list[AgentMetadata] = Field(default_factory=list)
    instance_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


# ── Run ───────────────────────────────────────────────────────────────────────

class Run(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id:         str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id:   str = ''
    session_id: str | None = None
    status:     RunStatus = RunStatus.CREATED
    mode:       RunMode = RunMode.STREAM
    input:      list[MessagePart] = Field(default_factory=list)
    output:     list[MessagePart] = Field(default_factory=list)
    error:      str | None = None
    metadata:   dict[str, Any] = Field(default_factory=dict)
    created_at:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    agent_id:   str = ''
    session_id: str | None = None
    mode:       RunMode = RunMode.STREAM
    input:      list[MessagePart] = Field(default_factory=list)
    metadata:   dict[str, Any] = Field(default_factory=dict)


# ── SSE / WebSocket events ────────────────────────────────────────────────────

class RunOutputEvent(BaseModel):
    model_config = ConfigDict(extra='ignore')
    type:   Literal['output', 'completed', 'error'] = 'output'
    run_id: str = ''
    part:   MessagePart | None = None   # present when type='output'
    error:  str | None = None           # present when type='error'


# ── Device authorization (RFC 8628) ──────────────────────────────────────────

class DeviceCodeResponse(BaseModel):
    model_config = ConfigDict(extra='ignore')
    device_code:      str = ''
    user_code:        str = ''
    verification_uri: str = ''
    expires_in:       int = 600     # seconds
    interval:         float = 5.0   # polling interval


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    device_code: str = ''


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra='ignore')
    access_token: str = ''


