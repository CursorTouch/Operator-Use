from typing import Any


def __getattr__(name: str) -> Any:
    if name == "Desktop":
        from .service import Desktop
        return Desktop
    if name == "LinuxDesktop":
        from .service import LinuxDesktop
        return LinuxDesktop
    raise AttributeError(name)

__all__ = ["Desktop", "LinuxDesktop"]
