from typing import TYPE_CHECKING

from operator_use.gateway.service import Gateway
from operator_use.bus.service import Bus
from operator_use.gateway.types import BaseChannel
from operator_use.bus.types import (
    IncomingMessage,
    OutgoingMessage,
    StreamPhase,
    TextPart,
    ImagePart,
    AudioPart,
    FilePart,
    ContentPart,
    text_from_parts,
    media_paths_from_parts,
)

# Channel classes are re-exported lazily: importing them eagerly pulls in
# optional third-party deps (aiohttp, discord.py, slack-bolt, twitchio,
# websockets). Since importing any `program.gateway.*` submodule runs this
# __init__, eager channel imports would make a single missing optional extra
# break the entire gateway/runtime/settings/test import graph. PEP 562
# __getattr__ defers each channel import to first actual use.

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
    return getattr(importlib.import_module(target[0]), target[1])


def __dir__() -> list[str]:
    return list(__all__)


__all__ = [
    'Gateway',
    'Bus',
    'BaseChannel',
    'IncomingMessage',
    'OutgoingMessage',
    'StreamPhase',
    'TextPart',
    'ImagePart',
    'AudioPart',
    'FilePart',
    'ContentPart',
    'text_from_parts',
    'media_paths_from_parts',
    'StdioChannel',
    'WebSocketChannel', 'WebSocketServer',
    'TelegramChannel', 'TelegramBot',
    'DiscordChannel', 'DiscordBot',
    'SlackChannel', 'SlackBot',
    'TwitchChannel',
]
