from typing import TYPE_CHECKING

from operator_use.channels.websocket.types import WebSocketChannelConfig

# Lazy: the service module imports the websockets package. Keep it out of the
# import graph until the channel is actually used (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.channels.websocket.service import WebSocketChannel, WebSocketServer

_LAZY = {'WebSocketChannel', 'WebSocketServer'}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        return getattr(
            importlib.import_module('operator_use.channels.websocket.service'), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['WebSocketChannel', 'WebSocketServer', 'WebSocketChannelConfig']
