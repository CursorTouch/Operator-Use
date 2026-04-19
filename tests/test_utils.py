"""Tests for utility helpers."""

import pytest
from pathlib import Path

from operator_use.utils.helper import is_binary_file, resolve, ensure_directory


# --- is_binary_file ---


def test_is_binary_file_text(tmp_path):
    f = tmp_path / "text.txt"
    f.write_text("hello world", encoding="utf-8")
    assert is_binary_file(f) is False


def test_is_binary_file_binary(tmp_path):
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\x00\x01\x02\x03")
    assert is_binary_file(f) is True


def test_is_binary_file_nonexistent(tmp_path):
    f = tmp_path / "ghost.txt"
    assert is_binary_file(f) is False


def test_is_binary_file_empty(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")
    assert is_binary_file(f) is False


# --- resolve ---

def test_resolve_absolute_path_inside_base(tmp_path):
    """Absolute path inside the base directory should resolve normally."""
    abs_path = tmp_path / "file.txt"
    result = resolve(tmp_path, abs_path)
    assert result == abs_path.resolve()


def test_resolve_absolute_path_outside_base_raises(tmp_path):
    """Absolute path outside the base must raise PermissionError (CWE-22 fix)."""
    abs_path = tmp_path / "file.txt"
    with pytest.raises(PermissionError, match="Path traversal blocked"):
        resolve("/some/other/base", abs_path)


def test_resolve_relative_path(tmp_path):
    result = resolve(tmp_path, "subdir/file.txt")
    assert result == (tmp_path / "subdir" / "file.txt").resolve()


def test_resolve_with_path_objects(tmp_path):
    base = Path(tmp_path)
    result = resolve(base, Path("child/file.py"))
    assert result == (tmp_path / "child" / "file.py").resolve()


def test_resolve_dot_path(tmp_path):
    result = resolve(tmp_path, ".")
    assert result == Path(tmp_path).resolve()


# --- ensure_directory ---


def test_ensure_directory_creates_new(tmp_path):
    new_dir = str(tmp_path / "new" / "nested" / "dir")
    result = ensure_directory(new_dir)
    assert Path(new_dir).exists()
    assert result == new_dir


def test_ensure_directory_existing_no_error(tmp_path):
    existing = str(tmp_path)
    result = ensure_directory(existing)
    assert result == existing


def test_ensure_directory_returns_path(tmp_path):
    d = str(tmp_path / "out")
    returned = ensure_directory(d)
    assert returned == d
