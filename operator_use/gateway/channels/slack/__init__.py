from typing import TYPE_CHECKING

from operator_use.gateway.channels.slack.types import SlackChannelConfig

# Lazy: the service module imports slack-bolt / aiohttp. Keep it out of the
# import graph until the channel is actually used (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.gateway.channels.slack.service import SlackChannel, SlackBot

_LAZY = {'SlackChannel', 'SlackBot'}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(
            importlib.import_module('operator_use.gateway.channels.slack.service'), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['SlackChannel', 'SlackBot', 'SlackChannelConfig']
