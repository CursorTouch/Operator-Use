from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SlackChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    allow_from: list[str] = Field(default_factory=list)  # user_id allowlist; empty = open
    reply_to_message: bool = True                        # reply-in-thread for response (Slack default behavior)
    show_tool_notifications: bool = True                 # send ⚙️ tool_start / ⚠️ tool_end notifications
    # Note: Slack already requires @mention to fire app_mention; no group_policy needed.
