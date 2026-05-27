"""Computer automation primitives routed to the current operating system."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any, Literal

PlatformName = Literal["linux", "macos", "windows"]

_PLATFORM_PACKAGES: dict[PlatformName, str] = {
    "linux": "operator_use.computer.linux",
    "macos": "operator_use.computer.macos",
    "windows": "operator_use.computer.windows",
}

_ROUTED_OBJECTS: dict[str, tuple[str, str]] = {
    "Desktop": ("desktop", "Desktop"),
    "WatchDog": ("watchdog", "WatchDog"),
}


def get_platform_name(platform: str | None = None) -> PlatformName:
    """Return the computer backend name for a Python platform string."""
    platform = platform or sys.platform
    match platform:
        case "darwin":
            return "macos"
        case "win" | "win32":
            return "windows"
        case "linux":
            return "linux"
    raise RuntimeError(f"Unsupported computer platform: {platform}")


def get_platform_package(platform: str | None = None) -> ModuleType:
    """Import and return the backend package for the current operating system."""
    name = get_platform_name(platform)
    return import_module(_PLATFORM_PACKAGES[name])


def get_desktop_class(platform: str | None = None) -> type[Any]:
    """Return the current platform's Desktop implementation class."""
    return _get_routed_object("Desktop", platform)


def _get_routed_object(name: str, platform: str | None = None) -> Any:
    if name not in _ROUTED_OBJECTS:
        raise AttributeError(name)
    module_suffix, attr = _ROUTED_OBJECTS[name]
    backend_name = get_platform_name(platform)
    module = import_module(f"{_PLATFORM_PACKAGES[backend_name]}.{module_suffix}")
    return getattr(module, attr)


def __getattr__(name: str) -> Any:
    return _get_routed_object(name)


__all__ = [
    "Desktop",
    "WatchDog",
]
