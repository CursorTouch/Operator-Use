"""Security tests for path traversal. Unskipped as fixes land per SECURITY_ROADMAP.md."""
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
            except PermissionError:
                pass  # expected

    def test_resolve_allows_valid_paths(self, workspace_dir):
        result = resolve(workspace_dir, "subdir/file.txt")
        assert result.is_relative_to(workspace_dir.resolve())
        result2 = resolve(workspace_dir, "file.txt")
        assert result2.is_relative_to(workspace_dir.resolve())
