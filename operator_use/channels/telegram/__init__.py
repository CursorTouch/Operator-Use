from typing import TYPE_CHECKING

from operator_use.channels.telegram.types import TelegramChannelConfig

# Lazy: the service module imports python-telegram-bot. Keep it out of the
# import graph until the channel is actually used (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.channels.telegram.service import TelegramChannel, TelegramBot

_LAZY = {'TelegramChannel', 'TelegramBot'}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(
            importlib.import_module('operator_use.channels.telegram.service'), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['TelegramChannel', 'TelegramBot', 'TelegramChannelConfig']
