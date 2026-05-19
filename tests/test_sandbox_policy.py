"""Tests for SandboxPolicy: presets, field defaults, and path resolution."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from program.sandbox.policy import SandboxPolicy, _detect_os_sandbox
from program.sandbox.service import Sandbox


# ── _detect_os_sandbox ────────────────────────────────────────────────────────

class TestDetectOsSandbox:
    def test_returns_string_or_none(self):
        result = _detect_os_sandbox()
        assert result is None or isinstance(result, str)

    def test_darwin_returns_sandbox_exec_or_none(self):
        if sys.platform == 'darwin':
            result = _detect_os_sandbox()
            assert result in (None, 'sandbox-exec')

    def test_linux_returns_bwrap_or_none(self):
        if sys.platform.startswith('linux'):
            result = _detect_os_sandbox()
            assert result in (None, 'bwrap')


# ── SandboxPolicy presets ─────────────────────────────────────────────────────

class TestSandboxPolicyPresets:
    def test_off_preset_mode(self):
        p = SandboxPolicy.off()
        assert p.mode == 'off'

    def test_permissive_preset_mode(self):
        p = SandboxPolicy.permissive()
        assert p.mode == 'warn'
        assert p.os_sandbox is None

    def test_strict_preset_mode(self, tmp_path):
        p = SandboxPolicy.strict(tmp_path)
        assert p.mode == 'enforce'
        assert p.os_sandbox == 'auto'

    def test_strict_includes_cwd_in_write_paths(self, tmp_path):
        p = SandboxPolicy.strict(tmp_path)
        assert str(tmp_path) in p.allowed_write_paths

    def test_strict_allows_tmp(self, tmp_path):
        p = SandboxPolicy.strict(tmp_path)
        assert '/tmp' in p.allowed_write_paths or '/private/tmp' in p.allowed_write_paths

    def test_strict_reads_unrestricted(self, tmp_path):
        p = SandboxPolicy.strict(tmp_path)
        assert p.allowed_read_paths is None


# ── SandboxPolicy defaults ────────────────────────────────────────────────────

class TestSandboxPolicyDefaults:
    def test_default_mode_enforce(self):
        p = SandboxPolicy()
        assert p.mode == 'enforce'

    def test_default_write_paths_none(self):
        p = SandboxPolicy()
        assert p.allowed_write_paths is None

    def test_default_read_paths_none(self):
        p = SandboxPolicy()
        assert p.allowed_read_paths is None

    def test_default_allow_shell(self):
        p = SandboxPolicy()
        assert p.allow_shell is True

    def test_default_allow_network(self):
        p = SandboxPolicy()
        assert p.allow_network is True

    def test_default_no_blocked_commands(self):
        p = SandboxPolicy()
        assert p.blocked_command_patterns == []


# ── Sandbox (service) ─────────────────────────────────────────────────────────

class TestSandboxInit:
    def test_off_policy_initialises(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.off(), tmp_path)
        assert sandbox is not None

    def test_permissive_policy_initialises(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.permissive(), tmp_path)
        assert sandbox is not None

    def test_strict_policy_initialises(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.strict(tmp_path), tmp_path)
        assert sandbox is not None

    def test_cwd_resolved_to_absolute(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.off(), tmp_path)
        assert sandbox._cwd.is_absolute()

    def test_os_tool_is_none_for_off_policy(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.off(), tmp_path)
        assert sandbox._os_tool is None

    def test_os_tool_is_none_for_permissive_policy(self, tmp_path):
        sandbox = Sandbox(SandboxPolicy.permissive(), tmp_path)
        assert sandbox._os_tool is None
