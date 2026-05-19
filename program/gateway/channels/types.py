from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from program.gateway.channels.telegram.types import TelegramChannelConfig
from program.gateway.channels.discord.types import DiscordChannelConfig
from program.gateway.channels.slack.types import SlackChannelConfig
from program.gateway.channels.websocket.types import WebSocketChannelConfig
from program.gateway.channels.twitch.types import TwitchChannelConfig
from program.gateway.channels.email.types import EmailChannelConfig


class ChannelsSettings(BaseModel):
    model_config = ConfigDict(extra='ignore')
    websocket: WebSocketChannelConfig = Field(default_factory=WebSocketChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    twitch: TwitchChannelConfig = Field(default_factory=TwitchChannelConfig)
    email: EmailChannelConfig = Field(default_factory=EmailChannelConfig)
