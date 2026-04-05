"""Security tests for terminal command controls. Unskipped as fixes land per SECURITY_ROADMAP.md."""

from operator_use.agent.tools.builtin.terminal import _is_command_allowed


class TestTerminalSecurity:
    def test_blocklist_blocks_rm_rf(self):
        # These should be blocked even if rm were in the allowlist — shell escape
        allowed, reason = _is_command_allowed("git status | bash -c 'rm -rf /'")
        assert not allowed
        assert "escape" in reason.lower() or "blocked" in reason.lower()

    def test_blocklist_blocks_shell_escape(self):
        for cmd in ["echo $(whoami)", "ls `id`", "git log | sh"]:
            allowed, reason = _is_command_allowed(cmd)
            assert not allowed, f"Should have blocked: {cmd!r}"

    def test_allowlist_permits_safe_commands(self):
        for cmd in ["git status", "ls -la", "pytest tests/", "python --version"]:
            allowed, reason = _is_command_allowed(cmd)
            assert allowed, f"Should have allowed: {cmd!r}, reason: {reason}"
