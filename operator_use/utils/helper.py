"""Helper utilities."""

from pathlib import Path
import os


def is_binary_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except (OSError, IOError):
        return False

def resolve(base: str | Path, path: str | Path) -> Path:
    """Resolve *path* relative to *base*, blocking traversal outside the workspace.

    Uses is_relative_to() (Python 3.12+) rather than startswith() to avoid
    prefix-collision bypasses (e.g. /workspace_evil passes startswith(/workspace)).
    """
    base_resolved = Path(base).resolve()
    resolved = (base_resolved / Path(path)).resolve()
    if not resolved.is_relative_to(base_resolved):
        raise PermissionError(
            f"Path traversal blocked: {path!r} resolves outside workspace {base_resolved}"
        )
    return resolved


def ensure_directory(path: str) -> str:
    """Create directory if it does not exist. Return the path."""
    os.makedirs(path, exist_ok=True)
    return path
