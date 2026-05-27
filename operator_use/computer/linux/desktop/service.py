from operator_use.computer.types import Desktop as BaseDesktop


class LinuxDesktop(BaseDesktop):
    """Placeholder for the Linux computer backend."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Linux computer backend is not implemented yet.")


# Backward-compatible alias used by the platform router in computer/__init__.py
Desktop = LinuxDesktop
