from typing import Any


def __getattr__(name: str) -> Any:
    if name == "Desktop":
        from .service import Desktop
        return Desktop
    if name == "WindowsDesktop":
        from .service import WindowsDesktop
        return WindowsDesktop
    raise AttributeError(name)

__all__ = ["Desktop", "WindowsDesktop"]
