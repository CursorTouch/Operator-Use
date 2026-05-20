from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WebSocketChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    host: str = '127.0.0.1'
    port: int = 8765
    streaming: bool = True
    streaming_latency: float = 1.0

