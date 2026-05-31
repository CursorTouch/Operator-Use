from typing import TYPE_CHECKING

from operator_use.gateway.channels.types import (
    ChannelsSettings,
    WebSocketChannelConfig,
    TelegramChannelConfig,
    DiscordChannelConfig,
    SlackChannelConfig,
    TwitchChannelConfig,
)

# Channel service classes pull in optional third-party deps (aiohttp,
# discord.py, slack-bolt, twitchio, websockets). Importing them eagerly here
# means a single missing optional extra breaks importing this whole package —
# and everything that transitively imports it (settings, runtime, tests),
# since `program.gateway.channels.types` triggers this __init__. They are
# loaded lazily on first attribute access (PEP 562) so the import graph stays
# robust; a missing dependency only surfaces if that channel is actually used.

_LAZY: dict[str, tuple[str, str]] = {
    'StdioChannel': ('operator_use.gateway.channels.stdio', 'StdioChannel'),
    'WebSocketChannel': ('operator_use.gateway.channels.websocket', 'WebSocketChannel'),
    'WebSocketServer': ('operator_use.gateway.channels.websocket', 'WebSocketServer'),
    'TelegramChannel': ('operator_use.gateway.channels.telegram', 'TelegramChannel'),
    'TelegramBot': ('operator_use.gateway.channels.telegram', 'TelegramBot'),
    'DiscordChannel': ('operator_use.gateway.channels.discord', 'DiscordChannel'),
    'DiscordBot': ('operator_use.gateway.channels.discord', 'DiscordBot'),
    'SlackChannel': ('operator_use.gateway.channels.slack', 'SlackChannel'),
    'SlackBot': ('operator_use.gateway.channels.slack', 'SlackBot'),
    'TwitchChannel': ('operator_use.gateway.channels.twitch', 'TwitchChannel'),
}

if TYPE_CHECKING:
    from operator_use.gateway.channels.stdio import StdioChannel
    from operator_use.gateway.channels.websocket import WebSocketChannel, WebSocketServer
    from operator_use.gateway.channels.telegram import TelegramChannel, TelegramBot
    from operator_use.gateway.channels.discord import DiscordChannel, DiscordBot
    from operator_use.gateway.channels.slack import SlackChannel, SlackBot
    from operator_use.gateway.channels.twitch import TwitchChannel


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module = importlib.import_module(target[0])
    return getattr(module, target[1])


def __dir__() -> list[str]:
    return list(__all__)


__all__ = [
    'ChannelsSettings',
    'WebSocketChannelConfig', 'TelegramChannelConfig', 'DiscordChannelConfig',
    'SlackChannelConfig', 'TwitchChannelConfig',
    'StdioChannel',
    'WebSocketChannel', 'WebSocketServer',
    'TelegramChannel', 'TelegramBot',
    'DiscordChannel', 'DiscordBot',
    'SlackChannel', 'SlackBot',
    'TwitchChannel',
]
