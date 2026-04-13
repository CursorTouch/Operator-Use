from operator_use.tools import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH
from pydantic import BaseModel, Field
from pathlib import Path
import asyncio
import sys
import os
import signal

BLOCKED_COMMANDS = {
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /*",
    "dd if=/dev/zero",
    "dd if=/dev/random",
    "mkfs",
    "fdisk",
    "parted",
    ":(){:|:&};:",
    "chmod 777 /",
    "chmod -R 777",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init 0",
    "init 6",
}


class Terminal(BaseModel):
    cmd: str = Field(
        description="The shell command to run. On Windows uses cmd.exe, on Linux/macOS uses bash. Chain commands with && for sequential execution. Avoid interactive commands that wait for input."
    )
    timeout: int = Field(
        ge=1,
        le=60,
        description="Timeout in seconds before the command is killed (1-60, default 10). Increase for slow operations like installs or builds.",
        default=10,
    )


def _is_command_blocked(cmd: str) -> str | None:
    """Return blocked pattern if cmd matches, else None."""
    normalized = " ".join(cmd.strip().split())
    for blocked in BLOCKED_COMMANDS:
        if blocked in normalized:
            return blocked
    return None


@Tool(
    name="terminal",
    description="Run a shell command and return stdout, stderr, and exit code. Use for git, package installs, running scripts, checking processes, or any CLI task. Commands run from the codebase root. Destructive commands (rm -rf /, format, shutdown, etc.) are blocked. For long outputs, results are truncated — pipe through head/tail if needed.",
    model=Terminal,
)
async def terminal(cmd: str, timeout: int = 10, **kwargs) -> str:
    blocked = _is_command_blocked(cmd)
    if blocked:
        return ToolResult.error_result(f"Command blocked: contains forbidden pattern '{blocked}'")

    env = os.environ.copy()

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
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.kill()
        await process.wait()
        return ToolResult.error_result(f"Command timed out after {timeout} seconds")

    stdout = stdout.decode("utf-8").strip()
    stderr = stderr.decode("utf-8").strip()
    exit_code = process.returncode

    lines = []
    if stdout.rstrip():
        lines.append("-- STDOUT --")
        lines.append(stdout.rstrip())
    if stderr.rstrip():
        lines.append("-- STDERR --")
        lines.append(stderr.rstrip())
    if exit_code != 0:
        lines.append(f"Exit code: {exit_code}")

    output = "\n".join(lines)
    if len(output) > MAX_TOOL_OUTPUT_LENGTH:
        output = output[:MAX_TOOL_OUTPUT_LENGTH] + "..."
    return ToolResult(
        success=exit_code == 0,
        output=output,
        error=stderr if exit_code != 0 else None,
        metadata={
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        },
    )
