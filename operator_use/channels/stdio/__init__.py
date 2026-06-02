from typing import TYPE_CHECKING

# Lazy for consistency with the other channels (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.channels.stdio.service import StdioChannel


def __getattr__(name: str):
    if name == 'StdioChannel':
        import importlib
        return importlib.import_module(
            'operator_use.channels.stdio.service'
        ).StdioChannel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['StdioChannel']
