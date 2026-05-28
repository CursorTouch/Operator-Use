from operator_use.computer.types import Desktop as BaseDesktop

_MSG = "Linux computer backend is not implemented yet."


class LinuxDesktop(BaseDesktop):
    """Placeholder for the Linux computer backend."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(_MSG)

    def open(self):
        raise NotImplementedError(_MSG)

    def close(self):
        raise NotImplementedError(_MSG)

    @property
    def is_open(self) -> bool:
        raise NotImplementedError(_MSG)

    def get_state(self, as_bytes=False):
        raise NotImplementedError(_MSG)

    def get_screen_size(self):
        raise NotImplementedError(_MSG)

    def get_windows(self):
        raise NotImplementedError(_MSG)

    def get_foreground_window(self):
        raise NotImplementedError(_MSG)

    def get_screenshot(self, as_bytes=False):
        raise NotImplementedError(_MSG)

    def click(self, loc, button="left", clicks=1):
        raise NotImplementedError(_MSG)

    def move(self, loc):
        raise NotImplementedError(_MSG)

    def drag(self, loc):
        raise NotImplementedError(_MSG)

    def scroll(self, loc=None, orientation="vertical", direction="down", wheel_times=1):
        raise NotImplementedError(_MSG)

    def type(self, loc, text, caret_position="idle", clear=False, press_enter=False):
        raise NotImplementedError(_MSG)

    def shortcut(self, shortcut):
        raise NotImplementedError(_MSG)

    def app(self, mode="launch", name=None, loc=None, size=None):
        raise NotImplementedError(_MSG)

    def wait(self, duration):
        raise NotImplementedError(_MSG)


# Backward-compatible alias used by the platform router in computer/__init__.py
Desktop = LinuxDesktop
