from operator_use.tools import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH
from operator_use.config.paths import get_named_workspace_dir
from pydantic import BaseModel, Field
from pathlib import Path
import asyncio
import sys
import os
import signal

from operator_use.tools._terminal_guards import _is_command_allowed


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
    cwd: str | None = Field(
        default=None,
        description="Working directory for the command. Absolute path or workspace-relative path (e.g. 'skills/youtube-cli/scripts'). Defaults to workspace root.",
    )


@Tool(
    name="terminal",
    description="Run a shell command and return stdout, stderr, and exit code. Use for git, package installs, running scripts, checking processes, or any CLI task. CWD is the workspace root — use the same paths as write_file/list_dir (e.g. 'python temp/script.py'). Commands not in the allowlist are blocked. For long outputs, results are truncated — pipe through head/tail if needed.",
    model=Terminal,
)
async def terminal(cmd: str, timeout: int = 10, cwd: str | None = None, **kwargs) -> str:
    allowed, reason = _is_command_allowed(cmd)
    if not allowed:
        return ToolResult.error_result(reason)

    env = os.environ.copy()

    if sys.platform == "win32":
        shell_cmd = ["cmd", "/c", cmd]
    else:
        shell_cmd = ["/bin/bash", "-c", cmd]

    workspace = kwargs.get("_workspace") or get_named_workspace_dir("operator")
    if cwd:
        resolved = Path(cwd) if Path(cwd).is_absolute() else Path(workspace) / cwd
        cwd = str(resolved)
    else:
        cwd = str(workspace)
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

    stdout = stdout.decode("utf-8", errors="replace").strip()
    stderr = stderr.decode("utf-8", errors="replace").strip()
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
