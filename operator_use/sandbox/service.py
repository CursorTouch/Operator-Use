from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from operator_use.sandbox.policy import SandboxPolicy, _detect_os_sandbox

if TYPE_CHECKING:
    from operator_use.hooks.service import Hooks

logger = logging.getLogger(__name__)

_WRITE_TOOLS   = {'write', 'edit_file'}
_READ_TOOLS    = {'read', 'ls', 'glob', 'grep'}
_NETWORK_TOOLS = {'web_fetch', 'web_search'}

# ── macOS Seatbelt profile ────────────────────────────────────────────────────
# Mirrors Claude Code's approach: allow-all baseline, deny file writes outside
# permitted subtrees.  Passed inline via sandbox-exec -p "...".
_MACOS_PROFILE = """\
(version 1)
(allow default)
(deny file-write* (regex ".*"))
(allow file-write*
{allow_rules}  (literal "/dev/null")
  (subpath "/dev")
  (subpath "/tmp")
  (subpath "/private/tmp"))
"""

# ── Linux bubblewrap command ──────────────────────────────────────────────────
# Mirrors Claude Code's Linux approach:
#   --ro-bind / /        whole FS read-only
#   --bind {path} {path} writable subtrees
#   --dev /dev           device nodes
#   --proc /proc         process filesystem
#   --tmpfs /tmp         fresh tmpfs (avoids leaking host /tmp)
_BWRAP_BASE = [
    'bwrap',
    '--ro-bind', '/', '/',
    '--dev', '/dev',
    '--proc', '/proc',
    '--tmpfs', '/tmp',
]


class Sandbox:
    """
    Enforces a SandboxPolicy before each tool call.

    Python-level checks cover all tools.
    OS-level subprocess sandboxing covers the terminal tool:
      - macOS  → sandbox-exec (Apple Seatbelt)
      - Linux  → bwrap (bubblewrap, same as Claude Code)
      - Windows → Python-level only (no kernel sandbox available without admin)

    Register once against a Hooks instance; the hook fires before every
    tool execution and can block or rewrite the invocation.
    """

    def __init__(self, policy: SandboxPolicy, cwd: Path) -> None:
        self._policy = policy
        self._cwd = cwd.resolve()

        # Resolve OS sandbox tool
        if policy.os_sandbox == 'auto':
            self._os_tool = _detect_os_sandbox()
        elif policy.os_sandbox in ('sandbox-exec', 'bwrap'):
            self._os_tool = policy.os_sandbox if shutil.which(policy.os_sandbox) else None
        else:
            self._os_tool = None

        self._allowed_write = self._resolve(policy.allowed_write_paths, extra=[self._cwd])
        self._allowed_read  = self._resolve(policy.allowed_read_paths)

        if self._os_tool:
            logger.debug('Sandbox: OS tool = %s', self._os_tool)

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, hooks: Hooks) -> None:
        if self._policy.mode == 'off':
            return
        hooks.register('tool_call', self._on_tool_call)
        logger.debug('Sandbox registered (mode=%s, os=%s)', self._policy.mode, self._os_tool)

    # ── Hook ──────────────────────────────────────────────────────────────────

    async def _on_tool_call(self, event):
        from operator_use.hooks.types import ToolCallEvent, ToolCallEventResult

        if not isinstance(event, ToolCallEvent):
            return None

        name   = event.tool_name
        params = event.input
        enforce = self._policy.mode == 'enforce'

        # ── Terminal ──────────────────────────────────────────────────────────
        if name == 'terminal':
            if not self._policy.allow_shell:
                return self._block('terminal tool is disabled by sandbox', enforce)

            cmd = params.get('cmd', '')
            bad = self._blocked_pattern(cmd)
            if bad:
                return self._block(f'command blocked by sandbox: {bad!r}', enforce)

            if self._os_tool:
                new_cmd = self._wrap_cmd(cmd)
                logger.debug('Sandbox: wrapping terminal cmd with %s', self._os_tool)
                return ToolCallEventResult(block=False, params={**params, 'cmd': new_cmd})

            return None

        # ── Network ───────────────────────────────────────────────────────────
        if name in _NETWORK_TOOLS and not self._policy.allow_network:
            return self._block(f'{name} blocked (network disabled)', enforce)

        # ── Write tools ───────────────────────────────────────────────────────
        if name in _WRITE_TOOLS and self._allowed_write is not None:
            path = self._extract_path(params)
            if path and not self._within(path, self._allowed_write):
                return self._block(
                    f'write to {path} is outside allowed paths '
                    f'({", ".join(str(p) for p in self._allowed_write)})',
                    enforce,
                )

        # ── Read tools ────────────────────────────────────────────────────────
        if name in _READ_TOOLS and self._allowed_read is not None:
            path = self._extract_path(params)
            if path and not self._within(path, self._allowed_read):
                return self._block(
                    f'read from {path} is outside allowed paths '
                    f'({", ".join(str(p) for p in self._allowed_read)})',
                    enforce,
                )

        return None

    # ── OS wrapper builders ───────────────────────────────────────────────────

    def _wrap_cmd(self, cmd: str) -> str:
        if self._os_tool == 'sandbox-exec':
            return self._macos_wrap(cmd)
        if self._os_tool == 'bwrap':
            return self._bwrap_wrap(cmd)
        return cmd

    def _macos_wrap(self, cmd: str) -> str:
        allow_rules = ''
        for p in (self._allowed_write or [self._cwd]):
            allow_rules += f'  (subpath {_sbpl_q(str(p))})\n'
        profile = _MACOS_PROFILE.format(allow_rules=allow_rules)
        return f'sandbox-exec -p {_sh_q(profile)} /bin/bash -c {_sh_q(cmd)}'

    def _bwrap_wrap(self, cmd: str) -> str:
        parts = list(_BWRAP_BASE)
        # Bind each writable path read-write
        for p in (self._allowed_write or [self._cwd]):
            parts += ['--bind', str(p), str(p)]
        # Optionally cut network
        if not self._policy.allow_network:
            parts += ['--unshare-net']
        parts += ['--', '/bin/bash', '-c', cmd]
        # Produce a shell-safe string so it can replace the cmd param
        return ' '.join(_sh_q(a) for a in parts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _block(self, reason: str, enforce: bool):
        from operator_use.hooks.types import ToolCallEventResult
        if enforce:
            logger.warning('Sandbox [blocked]: %s', reason)
            return ToolCallEventResult(block=True, reason=f'[Sandbox] {reason}')
        logger.warning('Sandbox [warn]: %s', reason)
        return None

    def _blocked_pattern(self, cmd: str) -> str | None:
        low = cmd.lower()
        for pat in self._policy.blocked_command_patterns:
            if pat.lower() in low:
                return pat
        return None

    def _extract_path(self, params: dict) -> Path | None:
        raw = params.get('path') or params.get('directory') or params.get('pattern')
        if not raw:
            return None
        p = Path(str(raw))
        return p.resolve() if p.is_absolute() else (self._cwd / p).resolve()

    def _within(self, path: Path, roots: list[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                pass
        return False

    @staticmethod
    def _resolve(paths: list[str] | None, extra: list[Path] | None = None) -> list[Path] | None:
        if paths is None:
            return list(extra) if extra else None
        result = [Path(p).resolve() for p in paths]
        for p in (extra or []):
            if p not in result:
                result.append(p)
        return result or None


# ── Quoting helpers ───────────────────────────────────────────────────────────

def _sh_q(s: str) -> str:
    """POSIX single-quote a string for shell."""
    return "'" + s.replace("'", "'\\''") + "'"


def _sbpl_q(s: str) -> str:
    """Double-quote a path for macOS SBPL."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
