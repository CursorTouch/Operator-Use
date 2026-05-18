from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebSocketChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    host: str = '127.0.0.1'
    port: int = 8765


class TelegramChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False


class DiscordChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False


class SlackChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False


class TwitchChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    channel_name: str = ''
    nick: str = ''
    prefix: str = '!'
    allow_from: list[str] = Field(default_factory=list)


class ChannelsSettings(BaseModel):
    model_config = ConfigDict(extra='ignore')
    websocket: WebSocketChannelConfig = Field(default_factory=WebSocketChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    twitch: TwitchChannelConfig = Field(default_factory=TwitchChannelConfig)
