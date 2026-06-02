from typing import TYPE_CHECKING

from operator_use.channels.types import (
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
# since `operator_use.channels.types` triggers this __init__. They are
# loaded lazily on first attribute access (PEP 562) so the import graph stays
# robust; a missing dependency only surfaces if that channel is actually used.

_LAZY: dict[str, tuple[str, str]] = {
    'StdioChannel': ('operator_use.channels.stdio', 'StdioChannel'),
    'WebSocketChannel': ('operator_use.channels.websocket', 'WebSocketChannel'),
    'WebSocketServer': ('operator_use.channels.websocket', 'WebSocketServer'),
    'TelegramChannel': ('operator_use.channels.telegram', 'TelegramChannel'),
    'TelegramBot': ('operator_use.channels.telegram', 'TelegramBot'),
    'DiscordChannel': ('operator_use.channels.discord', 'DiscordChannel'),
    'DiscordBot': ('operator_use.channels.discord', 'DiscordBot'),
    'SlackChannel': ('operator_use.channels.slack', 'SlackChannel'),
    'SlackBot': ('operator_use.channels.slack', 'SlackBot'),
    'TwitchChannel': ('operator_use.channels.twitch', 'TwitchChannel'),
}

if TYPE_CHECKING:
    from operator_use.channels.stdio import StdioChannel
    from operator_use.channels.websocket import WebSocketChannel, WebSocketServer
    from operator_use.channels.telegram import TelegramChannel, TelegramBot
    from operator_use.channels.discord import DiscordChannel, DiscordBot
    from operator_use.channels.slack import SlackChannel, SlackBot
    from operator_use.channels.twitch import TwitchChannel


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
