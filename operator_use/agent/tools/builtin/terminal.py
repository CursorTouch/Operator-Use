from operator_use.tools import Tool,ToolResult,MAX_TOOL_OUTPUT_LENGTH
from pydantic import BaseModel, Field
from pathlib import Path
import asyncio
import sys
import os
import signal

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


class Terminal(BaseModel):
    cmd: str=Field(description="The shell command to run. On Windows uses cmd.exe, on Linux/macOS uses bash. Chain commands with && for sequential execution. Avoid interactive commands that wait for input.")
    timeout: int = Field(ge=1,le=60,description="Timeout in seconds before the command is killed (1-60, default 10). Increase for slow operations like installs or builds.", default=10)


def _get_base_command(cmd: str) -> str:
    """Extract the base command name from a shell command string."""
    stripped = cmd.strip()
    if not stripped:
        return ""
    # Handle /usr/bin/git -> git, ./script.sh -> script.sh
    first_token = stripped.split()[0]
    return Path(first_token).name


def _is_command_allowed(cmd: str, extra_allowed: set[str] | None = None) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Checks shell escape patterns first, then base command allowlist.
    """
    # Check shell escape patterns
    cmd_lower = cmd.lower()
    for pattern in _SHELL_ESCAPE_PATTERNS:
        if pattern in cmd_lower:
            return False, f"Shell escape blocked: {pattern!r} in command"

    # Check for dangerous subcommands
    tokens = cmd_lower.split()
    for token in tokens[1:]:  # skip base command
        clean = token.strip(";&|")
        if clean in _SUBCOMMAND_BLOCKLIST:
            return False, f"Subcommand blocked: {clean!r}"

    # Check base command against allowlist
    base = _get_base_command(cmd)
    if not base:
        return False, "Empty command"

    allowed = _DEFAULT_ALLOWED | (extra_allowed or set())
    if base not in allowed:
        return False, f"Command not in allowlist: {base!r}. Add to terminal.extra_allowed_commands in config if needed."

    return True, ""


@Tool(name="terminal",description="Run a shell command and return stdout, stderr, and exit code. Use for git, package installs, running scripts, checking processes, or any CLI task. Commands run from the codebase root. Destructive commands (rm -rf /, format, shutdown, etc.) are blocked. For long outputs, results are truncated — pipe through head/tail if needed.", model=Terminal)
async def terminal(cmd: str, timeout: int = 10, **kwargs) -> str:
    allowed, reason = _is_command_allowed(cmd)
    if not allowed:
        return ToolResult.error_result(reason)

    env=os.environ.copy()

    if sys.platform == "win32":
        shell_cmd = ["cmd", "/c", cmd]
    else:
        shell_cmd = ["/bin/bash", "-c", cmd]

    workspace = Path.cwd()
    cwd = str(workspace) if workspace.exists() else str(Path.cwd())
    process = await asyncio.create_subprocess_exec(
        *shell_cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout,stderr=await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.kill()
        await process.wait()
        return ToolResult.error_result(f"Command timed out after {timeout} seconds")

    stdout=stdout.decode("utf-8").strip()
    stderr=stderr.decode("utf-8").strip()
    exit_code=process.returncode

    lines=[]
    if stdout.rstrip():
        lines.append("-- STDOUT --")
        lines.append(stdout.rstrip())
    if stderr.rstrip():
        lines.append("-- STDERR --")
        lines.append(stderr.rstrip())
    if exit_code != 0:
        lines.append(f"Exit code: {exit_code}")

    output="\n".join(lines)
    if len(output) > MAX_TOOL_OUTPUT_LENGTH:
        output=output[:MAX_TOOL_OUTPUT_LENGTH] + "..."
    return ToolResult(
        success=exit_code == 0,
        output=output,
        error=stderr if exit_code != 0 else None,
        metadata={
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
