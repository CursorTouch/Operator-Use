"""Security tests for terminal command controls. Unskipped as fixes land per SECURITY_ROADMAP.md."""
import pytest


class TestTerminalSecurity:
    @pytest.mark.skip(reason="Pending fix in issue #17 [Phase 1.2.1]")
    def test_blocklist_blocks_rm_rf(self):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #17 [Phase 1.2.1]")
    def test_blocklist_blocks_shell_escape(self):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #17 [Phase 1.2.1]")
    def test_allowlist_permits_safe_commands(self):
        pass
