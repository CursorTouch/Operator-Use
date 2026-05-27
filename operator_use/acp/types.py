from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: float = 5.0


class ACPAgentConfig(BaseModel):
    """One ACP agent entry from settings.json `acp.agents`."""
    model_config = ConfigDict(extra='ignore')

    enabled: bool = True
    name: str
    transport: Literal['stdio', 'http', 'webrtc'] = 'stdio'
    command: str | None = None       # stdio: executable name
    args: list[str] = Field(default_factory=list)
    url: str | None = None           # http: base URL; webrtc: room name


class ACPSettings(BaseModel):
    """Top-level `acp` block in settings.json."""
    model_config = ConfigDict(extra='ignore')

    enabled: bool = True
    agents: list[ACPAgentConfig] = Field(default_factory=list)
