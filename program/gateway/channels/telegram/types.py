from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TelegramChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
