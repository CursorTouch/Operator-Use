"""Security tests for path traversal. Unskipped as fixes land per SECURITY_ROADMAP.md."""
import os
import pytest
from operator_use.utils.helper import resolve
from tests.security.helpers import make_traversal_attempts


class TestPathTraversal:
    def test_resolve_blocks_absolute_path(self, workspace_dir):
        with pytest.raises(PermissionError, match="Path traversal blocked"):
            resolve(workspace_dir, "/etc/passwd")

    def test_resolve_blocks_parent_traversal(self, workspace_dir):
        for attempt in make_traversal_attempts():
            try:
                result = resolve(workspace_dir, attempt)
                # If it didn't raise, the path must be inside workspace
                assert result.is_relative_to(workspace_dir.resolve()), \
                    f"Traversal not blocked for {attempt!r}: got {result}"
            except (PermissionError, ValueError):
                pass  # PermissionError = traversal blocked; ValueError = null bytes rejected

    def test_resolve_allows_valid_paths(self, workspace_dir):
        result = resolve(workspace_dir, "subdir/file.txt")
        assert result.is_relative_to(workspace_dir.resolve())
        result2 = resolve(workspace_dir, "file.txt")
        assert result2.is_relative_to(workspace_dir.resolve())

    def test_resolve_blocks_symlink_escape(self, tmp_path):
        """Symlink pointing outside the workspace must be blocked after resolution."""
        import tempfile
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        # Create target outside workspace (sibling directory, not inside workspace)
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside = outside_dir / "secret.txt"
        outside.write_text("secret")
        link = workspace_dir / "evil_link"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")
        # resolve() follows symlinks — symlink to outside workspace must raise PermissionError
        with pytest.raises(PermissionError, match="Path traversal blocked"):
            resolve(workspace_dir, "evil_link")

    def test_resolve_handles_null_bytes(self, workspace_dir):
        """Null bytes in paths are handled safely — must not escape workspace."""
        attempt = "safe\x00../../etc/passwd"
        try:
            result = resolve(workspace_dir, attempt)
            assert result.is_relative_to(workspace_dir.resolve()), \
                f"Null byte traversal not blocked: {result}"
        except (PermissionError, ValueError):
            pass  # PermissionError = traversal blocked; ValueError = null bytes rejected by OS
