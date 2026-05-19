from typing import TYPE_CHECKING

from program.gateway.channels.twitch.types import TwitchChannelConfig

# Lazy: the service module imports twitchio. Keep it out of the import graph
# until the channel is actually used (see channels/__init__.py).
if TYPE_CHECKING:
    from program.gateway.channels.twitch.service import TwitchChannel

_LAZY = {'TwitchChannel'}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(
            importlib.import_module('program.gateway.channels.twitch.service'), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['TwitchChannel', 'TwitchChannelConfig']
