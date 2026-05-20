from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TwitchChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    channel_name: str = ''
    nick: str = ''
    prefix: str = '!'
    allow_from: list[str] = Field(default_factory=list)
    streaming: bool = True
    streaming_latency: float = 1.0

