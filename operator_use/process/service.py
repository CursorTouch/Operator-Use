from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from operator_use.process.views import ProcessSession


async def _read_output(session: ProcessSession) -> None:
    """Background task: drain stdout+stderr into session.output buffer."""
    try:
        while True:
            line = await session.process.stdout.readline()
            if not line:
                break
            session.output.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
    except Exception:
        pass


class ProcessStore:
    """Registry for background shell sessions — spawn, poll, log, write, clear."""

    def __init__(self) -> None:
        self._sessions: dict[str, ProcessSession] = {}

    async def spawn(self, cmd: str) -> ProcessSession:
        """Start a command in the background. Returns the new ProcessSession."""
        env = os.environ.copy()
        shell_args = ["cmd", "/c", cmd] if sys.platform == "win32" else ["/bin/bash", "-c", cmd]
        proc = await asyncio.create_subprocess_exec(
            *shell_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(Path.cwd()),
        )
        sid = uuid.uuid4().hex[:8]
        session = ProcessSession(session_id=sid, cmd=cmd, process=proc)
        session._reader = asyncio.ensure_future(_read_output(session))
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> ProcessSession | None:
        return self._sessions.get(session_id)

    def clear(self, session_id: str) -> bool:
        """Terminate and remove a session. Returns True if it existed."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        if session.is_running:
            try:
                session.process.terminate()
            except Exception:
                pass
        if session._reader and not session._reader.done():
            session._reader.cancel()
        return True


# ---------------------------------------------------------------------------
# Stateless OS-level helpers (no store needed)
# ---------------------------------------------------------------------------


async def list_os(filter: str | None = None) -> tuple[list[str], int]:
    """List running OS processes. Returns (rows, total_count)."""
    try:
        import psutil

        rows = []
        for p in psutil.process_iter(["pid", "name", "status", "memory_info"]):
            try:
                info = p.info
                if filter and filter.lower() not in info["name"].lower():
                    continue
                mem_mb = (info["memory_info"].rss / 1024 / 1024) if info["memory_info"] else 0
                rows.append(
                    f"PID={info['pid']:6}  {info['name'][:30]:<30}  "
                    f"status={info['status']:<10}  mem={mem_mb:.1f}MB"
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows, len(rows)
    except ImportError:
        cmd = ["tasklist"] if sys.platform == "win32" else ["ps", "aux"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        lines = out.decode("utf-8", errors="replace").splitlines()
        return lines, len(lines)


async def kill_os(pid: int | None, name: str | None) -> list[str]:
    """Kill OS processes by pid or name. Returns list of killed descriptions."""
    try:
        import psutil

        killed = []
        if pid is not None:
            p = psutil.Process(pid)
            p.terminate()
            killed.append(f"PID {pid} ({p.name()})")
        if name is not None:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if name.lower() in p.info["name"].lower():
                        p.terminate()
                        killed.append(f"PID {p.info['pid']} ({p.info['name']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        return killed
    except ImportError:
        if pid is not None:
            os.kill(pid, 15)
            return [f"PID {pid} (SIGTERM sent, psutil unavailable)"]
        return []
