from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscordChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    allow_from: list[str] = Field(default_factory=list)  # user_id allowlist; empty = open
    group_policy: Literal["mention", "open"] = "mention" # in servers, respond only when mentioned vs every message
    show_tool_calls: bool = True                         # send ⚙️ tool_start / ⚠️ tool_end notifications
    show_thinking: bool = False                          # stream model thinking text into the channel

    streaming: bool = True
    streaming_latency: float = 1.0
