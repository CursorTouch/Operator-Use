from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


def _detect_os_sandbox() -> str | None:
    """Return the best available OS-level sandbox tool for this platform."""
    if sys.platform == 'darwin':
        if shutil.which('sandbox-exec'):
            return 'sandbox-exec'
    elif sys.platform.startswith('linux'):
        if shutil.which('bwrap'):
            return 'bwrap'
    return None


@dataclass
class SandboxPolicy:
    """
    Declarative sandbox policy for the Operator harness.

    Modes
    -----
    off      No restrictions (default).
    warn     Log violations but never block.
    enforce  Block violations before tool execution.

    Presets
    -------
    SandboxPolicy.strict(cwd)   Writes locked to CWD; OS sandbox on if available.
    SandboxPolicy.permissive()  Warn-only; no OS sandbox.
    SandboxPolicy.off()         Completely disabled.
    """

    mode: Literal['off', 'warn', 'enforce'] = 'enforce'

    # ── Filesystem ────────────────────────────────────────────────────────────
    # Directories the agent may write to.  CWD is always appended at runtime.
    # None = unrestricted writes (Python-level check skipped).
    allowed_write_paths: list[str] | None = None

    # Directories the agent may read from.
    # None = unrestricted reads (Python-level check skipped).
    allowed_read_paths: list[str] | None = None

    # ── Shell ─────────────────────────────────────────────────────────────────
    allow_shell: bool = True
    blocked_command_patterns: list[str] = field(default_factory=list)

    # ── Network ───────────────────────────────────────────────────────────────
    allow_network: bool = True

    # ── OS-level subprocess sandbox ───────────────────────────────────────────
    # 'auto'         → pick the best available tool for this platform
    # 'sandbox-exec' → macOS Seatbelt (no root required)
    # 'bwrap'        → Linux bubblewrap (no root required)
    # None           → Python-level checks only
    os_sandbox: Literal['auto', 'sandbox-exec', 'bwrap'] | None = None

    # ── Presets ───────────────────────────────────────────────────────────────

    @classmethod
    def strict(cls, cwd: str | Path) -> SandboxPolicy:
        """
        Writes locked to CWD and /tmp.
        OS-level subprocess sandbox enabled when available
        (sandbox-exec on macOS, bwrap on Linux).
        """
        return cls(
            mode='enforce',
            allowed_write_paths=[str(cwd), '/tmp', '/private/tmp'],
            allowed_read_paths=None,
            allow_shell=True,
            allow_network=True,
            os_sandbox='auto',
        )

    @classmethod
    def permissive(cls) -> SandboxPolicy:
        """Log violations without blocking. No OS sandbox."""
        return cls(mode='warn', os_sandbox=None)

    @classmethod
    def off(cls) -> SandboxPolicy:
        return cls(mode='off')
