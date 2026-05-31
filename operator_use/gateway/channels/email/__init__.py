from typing import TYPE_CHECKING

# Lazy for consistency with the other channels (see channels/__init__.py).
if TYPE_CHECKING:
    from operator_use.gateway.channels.email.service import EmailChannel


def __getattr__(name: str):
    if name == 'EmailChannel':
        import importlib
        return importlib.import_module(
            'operator_use.gateway.channels.email.service'
        ).EmailChannel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['EmailChannel']
