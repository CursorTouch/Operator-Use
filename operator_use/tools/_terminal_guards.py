"""Pure security helpers for terminal command validation.

Intentionally import-free of operator_use machinery — safe to import
in tests without triggering the agent / tool registry cycle.
"""

from pathlib import Path

# Commands allowed by default. Deployments can extend via config:
# "terminal": {"extra_allowed_commands": ["make", "cargo"]}
_DEFAULT_ALLOWED = {
    "git", "ls", "cat", "head", "tail", "grep", "find", "echo", "pwd",
    "mkdir", "cp", "mv", "touch", "wc", "sort", "uniq", "cut", "tr",
    "pip", "pip3", "uv", "npm", "node", "npx", "yarn", "bun",
    "python", "python3", "pytest", "ruff", "mypy",
    "cargo", "go", "rustc",
    "curl", "wget",
    "docker", "kubectl",
    "env", "printenv", "which", "type", "man",
    "tar", "gzip", "gunzip", "zip", "unzip",
    "jq", "yq", "sed", "awk",
    "ssh", "scp", "rsync",
}

# Patterns that indicate shell escape — always blocked regardless of base command
_SHELL_ESCAPE_PATTERNS = [
    "| bash", "| sh", "| zsh", "| fish",
    "|bash", "|sh", "|zsh",
    "$(", "`",
    "&& bash", "&& sh",
]

_SUBCOMMAND_BLOCKLIST = {"eval", "exec", "source"}


def _get_base_command(cmd: str) -> str:
    """Extract the base command name from a shell command string."""
    stripped = cmd.strip()
    if not stripped:
        return ""
    first_token = stripped.split()[0]
    return Path(first_token).name


def _is_command_allowed(cmd: str, extra_allowed: set[str] | None = None) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Checks shell escape patterns first, then base command allowlist.
    """
    cmd_lower = cmd.lower()
    for pattern in _SHELL_ESCAPE_PATTERNS:
        if pattern in cmd_lower:
            return False, f"Shell escape blocked: {pattern!r} in command"

    tokens = cmd_lower.split()
    for token in tokens[1:]:
        clean = token.strip(";&|")
        if clean in _SUBCOMMAND_BLOCKLIST:
            return False, f"Subcommand blocked: {clean!r}"

    base = _get_base_command(cmd)
    if not base:
        return False, "Empty command"

    allowed = _DEFAULT_ALLOWED | (extra_allowed or set())
    if base not in allowed:
        return False, f"Command not in allowlist: {base!r}. Add to terminal.extra_allowed_commands in config if needed."

    return True, ""
