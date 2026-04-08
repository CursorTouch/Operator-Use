"""Security tests for path traversal. Unskipped as fixes land per SECURITY_ROADMAP.md."""
import pytest


class TestPathTraversal:
    @pytest.mark.skip(reason="Pending fix in issue #14 [Phase 1.1.1]")
    def test_resolve_blocks_absolute_path(self, workspace_dir):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #14 [Phase 1.1.1]")
    def test_resolve_blocks_parent_traversal(self, workspace_dir):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #14 [Phase 1.1.1]")
    def test_resolve_allows_valid_paths(self, workspace_dir):
        pass
