import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from pydantic import BaseModel, Field
from operator_use.tool.types import (
    Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult,
    ToolExecutionUpdateCallback, AbortSignal, ToolContext,
)

if TYPE_CHECKING:
    from operator_use.process.manager import ProcessManager

STREAM_FLUSH_INTERVAL = 0.1  # seconds between streamed partial updates

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
    detached: bool = Field(
        default=False,
        description=(
            "When True, start the command immediately as a background process and return "
            "its process ID without waiting. Use for servers, watchers, or any command "
            "that is meant to run indefinitely. The process tool can then be used to read "
            "output or stop it."
        ),
    )

class TerminalTool(Tool):
    def __init__(self):
        super().__init__(
            name="terminal",
            description=(
                "Run a shell command and return stdout, stderr, and exit code. "
                "Use for git, package installs, running scripts, or any CLI task. "
                "Destructive commands are blocked. "
                "If a command is still running when the timeout expires it is moved "
                "to a background process instead of killed — use the process tool to "
                "check its output or stop it."
            ),
            schema=TerminalSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Parallel,        )
        self._manager: 'ProcessManager | None' = None
        self._execute_path: str | None = None
        self._execute_command_prefix: str | None = None

    def _is_command_blocked(self, cmd: str) -> str | None:
        """Return blocked pattern if cmd matches, else None."""
        normalized = " ".join(cmd.strip().split())
        for blocked in BLOCKED_COMMANDS:
            if blocked in normalized:
                return blocked
        return None

    async def _kill_process_group(self, process) -> None:
        """Terminate the subprocess and every child it spawned.

        The command runs via a shell wrapper (and often `uv run`), which does
        not forward signals to its grandchildren. Killing only the wrapper
        leaves orphaned servers running, so we signal the whole process group.
        """
        def _signal_group(sig: int) -> bool:
            try:
                if sys.platform == "win32":
                    process.send_signal(sig)
                else:
                    os.killpg(os.getpgid(process.pid), sig)
                return True
            except (ProcessLookupError, PermissionError):
                return False

        if not _signal_group(signal.SIGTERM):
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
            return
        except asyncio.TimeoutError:
            pass
        _signal_group(signal.SIGKILL)
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: Optional[ToolExecutionUpdateCallback] = None,
        signal: Optional[AbortSignal] = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = invocation.params
        cmd = params.get("cmd")
        timeout = params.get("timeout", 10)
        cwd_param = params.get("cwd")
        detached = params.get("detached", False)

        if not cmd:
             return ToolResult.error(id=invocation.id, content="Parameter 'cmd' is required.")

        blocked = self._is_command_blocked(cmd)
        if blocked:
            return ToolResult.error(id=invocation.id, content=f"Command blocked: contains forbidden pattern '{blocked}'")

        profile_root = invocation.cwd or "."
        if cwd_param:
            resolved = Path(cwd_param) if Path(cwd_param).is_absolute() else Path(profile_root) / cwd_param
            cwd = str(resolved)
        else:
            cwd = str(profile_root)

        if detached:
            manager = self._manager or (context.process_manager if context else None)
            if manager is None:
                return ToolResult.error(id=invocation.id, content="detached=True requires the process manager (not available in this context).")
            desc = (cmd[:80] + '...') if len(cmd) > 80 else cmd
            record = await manager.create(command=cmd, description=desc, cwd=cwd)
            return ToolResult.ok(
                id=invocation.id,
                content=f"Background process started as `{record.id}`.\nUse the process tool to read output or stop it.",
                metadata={'process_id': record.id},
            )

        env = os.environ.copy()

        effective_cmd = f"{self._execute_command_prefix} {cmd}" if self._execute_command_prefix else cmd
        if sys.platform == "win32":
            shell_bin = self._execute_path or "cmd"
            shell_cmd = [shell_bin, "/c", effective_cmd]
        else:
            shell_bin = self._execute_path or "/bin/bash"
            shell_cmd = [shell_bin, "-c", effective_cmd]

        try:
            process = await asyncio.create_subprocess_exec(
                *shell_cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            out_buf = bytearray()
            err_buf = bytearray()

            cb = tool_execution_update_callback
            loop = asyncio.get_event_loop()
            _pending: list[str] = []
            _last_flush = 0.0
            _flush_lock = asyncio.Lock()

            async def _flush_stream(force: bool = False) -> None:
                nonlocal _last_flush
                if cb is None:
                    return
                async with _flush_lock:
                    if not _pending:
                        return
                    now = loop.time()
                    if not force and (now - _last_flush) < STREAM_FLUSH_INTERVAL:
                        return
                    delta = "".join(_pending)
                    _pending.clear()
                    _last_flush = now
                    try:
                        await cb(ToolResult(
                            id=invocation.id,
                            content=delta,
                            metadata={"stream": True},
                        ))
                    except Exception:
                        pass

            async def _drain(stream, buf: bytearray) -> None:
                if stream is None:
                    return
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if cb is not None:
                        _pending.append(chunk.decode("utf-8", errors="replace"))
                        await _flush_stream()

            readers = asyncio.gather(
                _drain(process.stdout, out_buf),
                _drain(process.stderr, err_buf),
            )

            timed_out = False
            try:
                # Python 3.12+: wait_for cancels and fully awaits `readers` before
                # raising TimeoutError — no shield needed and no manual cancel required.
                await asyncio.wait_for(readers, timeout=float(timeout))
            except asyncio.TimeoutError:
                timed_out = True
                if self._manager is not None:
                    # `readers` is already cancelled and done at this point.
                    await _flush_stream(force=True)
                    pre_captured = bytes(out_buf) + bytes(err_buf)
                    desc = (cmd[:80] + '...') if len(cmd) > 80 else cmd
                    record = await self._manager.adopt(
                        proc=process,
                        command=cmd,
                        description=desc,
                        cwd=cwd,
                        pre_captured=pre_captured,
                    )
                    partial = bytes(out_buf).decode('utf-8', errors='replace').strip()
                    lines = [
                        f"Command still running after {timeout}s — moved to background process `{record.id}`.",
                        "Use the process tool (action='output') to read output, or action='stop' to kill it.",
                    ]
                    if partial:
                        lines.append(f"-- PARTIAL OUTPUT --\n{partial}")
                    return ToolResult.ok(
                        id=invocation.id,
                        content="\n".join(lines),
                        metadata={'process_id': record.id, 'backgrounded': True},
                    )
                else:
                    await self._kill_process_group(process)
                    # readers is already cancelled (wait_for did it); nothing more to do.

            await process.wait()
            await _flush_stream(force=True)

            stdout_str = bytes(out_buf).decode("utf-8", errors="replace").strip()
            stderr_str = bytes(err_buf).decode("utf-8", errors="replace").strip()
            exit_code = process.returncode

            if timed_out:
                lines = [f"Command timed out after {timeout} seconds (process killed)."]
                if stdout_str:
                    lines.append("-- PARTIAL STDOUT (before kill) --")
                    lines.append(stdout_str)
                if stderr_str:
                    lines.append("-- PARTIAL STDERR (before kill) --")
                    lines.append(stderr_str)
                output = "\n".join(lines)
                if len(output) > MAX_TOOL_OUTPUT_LENGTH:
                    output = output[:MAX_TOOL_OUTPUT_LENGTH] + "..."
                return ToolResult.error(
                    id=invocation.id,
                    content=output,
                    metadata={"exit_code": exit_code, "stdout": stdout_str, "stderr": stderr_str, "timed_out": True},
                )

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
tool = TerminalTool()
