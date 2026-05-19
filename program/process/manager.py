from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from program.process.types import ProcessRecord, ProcessStatus

logger = logging.getLogger(__name__)

_MAX_BUFFER_BYTES = 1_000_000  # 1 MB cap per process in memory


class ProcessManager:
    """
    Manages long-running background shell processes for the agent.

    Output (stdout + stderr) is captured via pipes and stored in an
    in-memory ring buffer per process (capped at 1 MB). Nothing is
    written to disk.
    """

    def __init__(self, default_cwd: str | None = None) -> None:
        self._default_cwd = default_cwd or str(Path.cwd())
        self._records: dict[str, ProcessRecord] = {}
        self._subprocesses: dict[str, asyncio.subprocess.Process] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._buffers: dict[str, bytearray] = {}

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        command: str,
        description: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessRecord:
        pid = f"p{uuid4().hex[:8]}"
        now = time.time()

        record = ProcessRecord(
            id=pid,
            command=command,
            description=description,
            status=ProcessStatus.RUNNING,
            cwd=cwd or self._default_cwd,
            created_at=now,
            started_at=now,
            env=env,
        )

        merged_env = {**os.environ}
        if env:
            merged_env.update(env)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=record.cwd,
            env=merged_env,
        )

        self._records[pid] = record
        self._subprocesses[pid] = proc
        self._buffers[pid] = bytearray()
        self._watchers[pid] = asyncio.create_task(
            self._watch(pid, proc),
            name=f'process:watch:{pid}',
        )
        logger.debug('Process %s started: %s', pid, command)
        return record

    # ── Watch ─────────────────────────────────────────────────────────────────

    async def _watch(self, pid: str, proc: asyncio.subprocess.Process) -> None:
        buf = self._buffers[pid]
        if proc.stdout is not None:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > _MAX_BUFFER_BYTES:
                    del buf[:len(buf) - _MAX_BUFFER_BYTES]

        return_code = await proc.wait()
        record = self._records.get(pid)
        if record is None:
            return
        record.ended_at = time.time()
        record.return_code = return_code
        record.status = ProcessStatus.COMPLETED if return_code == 0 else ProcessStatus.FAILED
        self._subprocesses.pop(pid, None)
        logger.debug('Process %s exited with code %d', pid, return_code)

    # ── Stop ──────────────────────────────────────────────────────────────────

    async def stop(self, process_id: str) -> ProcessRecord:
        record = self._records.get(process_id)
        if record is None:
            raise KeyError(f"No process with id '{process_id}'")

        proc = self._subprocesses.get(process_id)
        if proc is not None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass

        watcher = self._watchers.pop(process_id, None)
        if watcher and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

        self._subprocesses.pop(process_id, None)
        record.ended_at = time.time()
        record.status = ProcessStatus.KILLED
        logger.debug('Process %s killed', process_id)
        return record

    # ── Query ─────────────────────────────────────────────────────────────────

    def get(self, process_id: str) -> ProcessRecord | None:
        return self._records.get(process_id)

    def list(self, status: ProcessStatus | None = None) -> list[ProcessRecord]:
        records = list(self._records.values())
        if status is not None:
            records = [r for r in records if r.status == status]
        return records

    def read_output(self, process_id: str, max_bytes: int = 12000) -> str:
        buf = self._buffers.get(process_id)
        if buf is None:
            raise KeyError(f"No process with id '{process_id}'")
        data = bytes(buf[-max_bytes:]) if len(buf) > max_bytes else bytes(buf)
        return data.decode('utf-8', errors='replace')

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Kill all running processes on shutdown."""
        for pid in list(self._subprocesses.keys()):
            try:
                await self.stop(pid)
            except Exception:
                logger.debug('Error stopping process %s on close', pid, exc_info=True)
