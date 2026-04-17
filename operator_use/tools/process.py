"""Process tool — list/kill OS processes and manage background shell sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from operator_use.tools import Tool, ToolResult, MAX_TOOL_OUTPUT_LENGTH
from operator_use.process.service import list_os, kill_os


class Process(BaseModel):
    action: Literal["list", "kill", "spawn", "poll", "log", "write", "clear"] = Field(
        description=(
            "list — show running OS processes. "
            "kill — stop a process by pid or name. "
            "spawn — start a command in the background (returns session_id). "
            "poll — check status + recent output of a background session. "
            "log — get full output of a background session. "
            "write — send text to stdin of a running background session. "
            "clear — remove a finished session from the registry."
        )
    )
    filter: Optional[str] = Field(default=None, description="Filter process names (list action)")
    pid: Optional[int] = Field(default=None, description="PID to kill")
    name: Optional[str] = Field(
        default=None, description="Process name to kill (kills all matches)"
    )
    cmd: Optional[str] = Field(
        default=None, description="Shell command to run in background (spawn action)"
    )
    session_id: Optional[str] = Field(
        default=None, description="Background session ID (from spawn)"
    )
    input: Optional[str] = Field(default=None, description="Text to send to stdin (write action)")
    lines: int = Field(default=30, description="Number of tail lines to return (poll/log actions)")


@Tool(
    name="process",
    description=(
        "Manage OS processes and background shell sessions. "
        "Use 'list' to see running processes, 'kill' to stop one by pid or name. "
        "Use 'spawn' to start a long-running command in the background (e.g. a server) — "
        "it returns a session_id you can use with 'poll', 'log', 'write', or 'clear'. "
        "Background sessions persist until you 'clear' them or the agent stops."
    ),
    model=Process,
)
async def process(
    action: str,
    filter: str | None = None,
    pid: int | None = None,
    name: str | None = None,
    cmd: str | None = None,
    session_id: str | None = None,
    input: str | None = None,
    lines: int = 30,
    **kwargs,
) -> ToolResult:
    store = kwargs.get("_process_store")

    match action:
        case "list":
            rows, total = await list_os(filter)
            if not rows:
                return ToolResult.success_result(
                    "No processes found" + (f" matching '{filter}'" if filter else "")
                )
            header = f"{'PID':>6}  {'NAME':<30}  {'STATUS':<10}  MEMORY\n" + "-" * 70
            output = header + "\n" + "\n".join(rows)
            if len(output) > MAX_TOOL_OUTPUT_LENGTH:
                output = output[:MAX_TOOL_OUTPUT_LENGTH] + f"\n... ({total} total, truncated)"
            return ToolResult.success_result(output)

        case "kill":
            if pid is None and name is None:
                return ToolResult.error_result("Provide pid or name to kill")
            try:
                killed = await kill_os(pid, name)
            except Exception as e:
                return ToolResult.error_result(str(e))
            if not killed:
                return ToolResult.error_result(f"No process found matching name='{name}'")
            return ToolResult.success_result(f"Terminated: {', '.join(killed)}")

        case "spawn":
            if not store:
                return ToolResult.error_result("ProcessStore not available (internal error)")
            if not cmd:
                return ToolResult.error_result("Provide cmd to spawn")
            session = await store.spawn(cmd)
            return ToolResult.success_result(
                f"Spawned background process (session_id={session.session_id})\n"
                f"cmd: {cmd}\nPID: {session.process.pid}\n"
                f"Use process(action='poll', session_id='{session.session_id}') to check status."
            )

        case "poll" | "log" | "write" | "clear":
            if not store:
                return ToolResult.error_result("ProcessStore not available (internal error)")
            if not session_id:
                return ToolResult.error_result(f"Provide session_id for action '{action}'")
            session = store.get(session_id)
            if session is None:
                return ToolResult.error_result(f"No session found with id '{session_id}'")

            match action:
                case "poll":
                    status = (
                        "running" if session.is_running else f"exited (code={session.exit_code})"
                    )
                    age = (datetime.now() - session.started_at).seconds
                    tail = session.tail(lines)
                    return ToolResult.success_result(
                        f"session_id={session_id}  status={status}  age={age}s  lines_buffered={len(session.output)}\n"
                        f"cmd: {session.cmd}\n"
                        f"--- last {lines} lines ---\n{tail or '(no output yet)'}"
                    )

                case "log":
                    full = session.full_log()
                    if not full:
                        return ToolResult.success_result("(no output buffered yet)")
                    if len(full) > MAX_TOOL_OUTPUT_LENGTH:
                        full = full[-MAX_TOOL_OUTPUT_LENGTH:] + "\n... (truncated)"
                    return ToolResult.success_result(full)

                case "write":
                    if not session.is_running:
                        return ToolResult.error_result(
                            f"Session {session_id} is not running (exit_code={session.exit_code})"
                        )
                    if not input:
                        return ToolResult.error_result("Provide input text to write")
                    try:
                        payload = (input if input.endswith("\n") else input + "\n").encode()
                        session.process.stdin.write(payload)
                        await session.process.stdin.drain()
                        return ToolResult.success_result(
                            f"Sent {len(payload)} bytes to session {session_id}"
                        )
                    except Exception as e:
                        return ToolResult.error_result(f"Write failed: {e}")

                case "clear":
                    store.clear(session_id)
                    return ToolResult.success_result(f"Session {session_id} removed from registry")

        case _:
            return ToolResult.error_result(f"Unknown action '{action}'")
