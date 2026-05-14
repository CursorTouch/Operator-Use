from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

MAX_TOOL_OUTPUT_LENGTH = 100000

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

class TerminalSchema(BaseModel):
    cmd: str = Field(
        description="The shell command to run. On Windows uses cmd.exe, on Linux/macOS uses bash. Chain commands with && for sequential execution. Avoid interactive commands that wait for input."
    )
    timeout: int = Field(
        ge=1,
        le=60,
        description="Timeout in seconds before the command is killed (1-60, default 10).",
        default=10,
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory for the command. Absolute path or relative to current directory.",
    )

class TerminalTool(Tool):
    def __init__(self):
        super().__init__(
            name="terminal",
            description="Run a shell command and return stdout, stderr, and exit code. Use for git, package installs, running scripts, or any CLI task. Destructive commands are blocked.",
            schema=TerminalSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Parallel
        )

    def _is_command_blocked(self, cmd: str) -> str | None:
        """Return blocked pattern if cmd matches, else None."""
        normalized = " ".join(cmd.strip().split())
        for blocked in BLOCKED_COMMANDS:
            if blocked in normalized:
                return blocked
        return None

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        cmd = params.get("cmd")
        timeout = params.get("timeout", 10)
        cwd_param = params.get("cwd")
        
        if not cmd:
             return ToolResult.error(id=invocation.id, content="Parameter 'cmd' is required.")

        blocked = self._is_command_blocked(cmd)
        if blocked:
            return ToolResult.error(id=invocation.id, content=f"Command blocked: contains forbidden pattern '{blocked}'")

        env = os.environ.copy()

        if sys.platform == "win32":
            shell_cmd = ["cmd", "/c", cmd]
        else:
            shell_cmd = ["/bin/bash", "-c", cmd]

        profile_root = kwargs.get("_profile") or "."
        if cwd_param:
            resolved = Path(cwd_param) if Path(cwd_param).is_absolute() else Path(profile_root) / cwd_param
            cwd = str(resolved)
        else:
            cwd = str(profile_root)

        try:
            process = await asyncio.create_subprocess_exec(
                *shell_cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=float(timeout))
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                await process.wait()
                return ToolResult.error(id=invocation.id, content=f"Command timed out after {timeout} seconds")

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode

            lines = []
            if stdout_str:
                lines.append("-- STDOUT --")
                lines.append(stdout_str)
            if stderr_str:
                lines.append("-- STDERR --")
                lines.append(stderr_str)
            if exit_code != 0:
                lines.append(f"Exit code: {exit_code}")

            output = "\n".join(lines)
            if not output:
                output = f"Command exited with code {exit_code} (no output)"
                
            if len(output) > MAX_TOOL_OUTPUT_LENGTH:
                output = output[:MAX_TOOL_OUTPUT_LENGTH] + "..."
            
            metadata = {
                "exit_code": exit_code,
                "stdout": stdout_str,
                "stderr": stderr_str,
            }

            if exit_code == 0:
                return ToolResult.ok(id=invocation.id, content=output, metadata=metadata)
            else:
                return ToolResult.error(id=invocation.id, content=output, metadata=metadata)

        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to execute command: {e}")