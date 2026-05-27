import pytest

from operator_use import computer


def test_get_platform_name_maps_supported_platforms():
    assert computer.get_platform_name("darwin") == "macos"
    assert computer.get_platform_name("win32") == "windows"
    assert computer.get_platform_name("linux") == "linux"


def test_get_platform_name_rejects_unsupported_platform():
    with pytest.raises(RuntimeError, match="Unsupported computer platform"):
        computer.get_platform_name("freebsd")


def test_linux_desktop_placeholder_routes_cleanly():
    desktop_cls = computer.get_desktop_class("linux")
    with pytest.raises(NotImplementedError, match="Linux computer backend"):
        desktop_cls()
