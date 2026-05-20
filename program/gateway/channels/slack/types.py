from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SlackChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    allow_from: list[str] = Field(default_factory=list)  # user_id allowlist; empty = open
    show_tool_calls: bool = True                         # send ⚙️ tool_start / ⚠️ tool_end notifications
    # Note: Slack already requires @mention to fire app_mention; no group_policy needed.

    streaming: bool = True
    streaming_latency: float = 1.0

