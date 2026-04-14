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
    """
    Resolves a path relative to a base path.
    If the path is absolute, it returns the path as is.
    Otherwise, it joins the path with the base path and returns the resolved path.
    """
    path = Path(path)
    if path.is_absolute():
        return path.resolve()
    return Path(base).joinpath(path).resolve()


def ensure_directory(path: str) -> str:
    """Create directory if it does not exist. Return the path."""
    os.makedirs(path, exist_ok=True)
    return path
