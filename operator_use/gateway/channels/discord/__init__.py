from typing import TYPE_CHECKING

from operator_use.gateway.channels.discord.types import DiscordChannelConfig

# Lazy: the service module imports discord.py. Keep it out of the import graph
# until the channel is actually used (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.gateway.channels.discord.service import DiscordChannel, DiscordBot

_LAZY = {'DiscordChannel', 'DiscordBot'}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(
            importlib.import_module('operator_use.gateway.channels.discord.service'), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['DiscordChannel', 'DiscordBot', 'DiscordChannelConfig']
