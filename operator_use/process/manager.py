from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from operator_use.process.types import ProcessRecord, ProcessStatus

logger = logging.getLogger(__name__)

_MAX_BUFFER_BYTES = 1_000_000  # 1 MB cap per shell process in memory

CompletionListener = Callable[[ProcessRecord], Awaitable[None] | None]


class ProcessManager:
    """
    Manages long-running background processes for the agent.

    Two process types:
      shell  — arbitrary shell commands; output captured in an in-memory ring
               buffer (1 MB cap).  No stdin interaction.
      agent  — spawns `operator acp serve` via ACPClient stdio; output written
               to a disk log file under tasks_dir.  Supports multi-turn
               interaction via write().
    """

    def __init__(
        self,
        default_cwd: str | None = None,
        tasks_dir: Path | None = None,
        max_terminated: int = 100,
        terminated_grace_s: float = 600.0,
    ) -> None:
        self._default_cwd = default_cwd or str(Path.cwd())
        self._tasks_dir = tasks_dir
        # Cap on retained terminated processes so a long-lived gateway doesn't
        # accumulate records/buffers (up to 1 MB each) without bound. Running
        # processes are never evicted.
        self._max_terminated = max_terminated
        # Grace window before a finished process is eligible for eviction. The
        # agent observes results by polling the process tool (there is no push
        # notification), so a just-finished process must stay readable long
        # enough to be polled — even if that means temporarily exceeding the cap.
        self._terminated_grace_s = terminated_grace_s
        if tasks_dir is not None:
            tasks_dir.mkdir(parents=True, exist_ok=True)

        # shell process state
        self._records: dict[str, ProcessRecord] = {}
        self._subprocesses: dict[str, asyncio.subprocess.Process] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._buffers: dict[str, bytearray] = {}

        # agent process state
        self._agent_queues: dict[str, asyncio.Queue[str | None]] = {}
        self._agent_session_tasks: dict[str, asyncio.Task] = {}

        # shared
        self._completion_listeners: dict[str, CompletionListener] = {}

    # ── Shell: Create ─────────────────────────────────────────────────────────

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
            type='shell',
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
            self._watch_shell(pid, proc),
            name=f'process:shell:{pid}',
        )
        logger.debug('Shell process %s started: %s', pid, command)
        return record

    # ── Shell: Adopt ──────────────────────────────────────────────────────────

    async def adopt(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        description: str,
        cwd: str,
        pre_captured: bytes = b'',
    ) -> ProcessRecord:
        """Take ownership of an already-running subprocess (e.g. terminal tool timeout)."""
        pid = f"p{uuid4().hex[:8]}"
        now = time.time()

        record = ProcessRecord(
            id=pid,
            type='shell',
            command=command,
            description=description,
            status=ProcessStatus.RUNNING,
            cwd=cwd,
            created_at=now,
            started_at=now,
        )

        self._records[pid] = record
        self._subprocesses[pid] = proc
        self._buffers[pid] = bytearray(pre_captured)
        self._watchers[pid] = asyncio.create_task(
            self._watch_adopted(pid, proc),
            name=f'process:adopted:{pid}',
        )
        logger.debug('Shell process %s adopted: %s', pid, command)
        return record

    # ── Agent: Create ─────────────────────────────────────────────────────────

    async def create_agent(
        self,
        prompt: str,
        description: str,
        provider: str,
        model: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessRecord:
        """Spawn a background agent via `operator acp serve` and send an initial prompt.

        The ACP session is held open; use write() to send follow-up prompts.
        Output is appended to a disk log file under tasks_dir.
        """
        pid = f"a{uuid4().hex[:8]}"
        now = time.time()

        output_file: Path | None = None
        if self._tasks_dir is not None:
            output_file = self._tasks_dir / f"{pid}.log"
            output_file.write_text('', encoding='utf-8')

        record = ProcessRecord(
            id=pid,
            type='agent',
            command=None,
            description=description,
            status=ProcessStatus.RUNNING,
            cwd=cwd or self._default_cwd,
            created_at=now,
            started_at=now,
            prompt=prompt,
            output_file=output_file,
            env=env,
        )

        self._records[pid] = record

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._agent_queues[pid] = queue
        await queue.put(prompt)

        self._agent_session_tasks[pid] = asyncio.create_task(
            self._run_agent_session(pid, provider, model, env),
            name=f'process:agent:{pid}',
        )
        logger.debug('Agent process %s started | provider=%s model=%s', pid, provider, model)
        return record

    # ── Agent: Write ──────────────────────────────────────────────────────────

    async def write(self, process_id: str, text: str) -> None:
        """Send a follow-up prompt to a running agent process."""
        record = self._records.get(process_id)
        if record is None:
            raise KeyError(f"No process with id '{process_id}'")
        if record.type != 'agent':
            raise ValueError(f"Process '{process_id}' is a shell process and does not accept input.")
        if record.status != ProcessStatus.RUNNING:
            raise ValueError(f"Process '{process_id}' is not running (status={record.status.value}).")
        await self._agent_queues[process_id].put(text)

    # ── Shared: Stop ──────────────────────────────────────────────────────────

    async def stop(self, process_id: str) -> ProcessRecord:
        record = self._records.get(process_id)
        if record is None:
            raise KeyError(f"No process with id '{process_id}'")

        if record.type == 'agent':
            return await self._stop_agent(process_id, record)

        # shell
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

        record.ended_at = time.time()
        record.status = ProcessStatus.KILLED
        self._subprocesses.pop(process_id, None)

        watcher = self._watchers.pop(process_id, None)
        if watcher and not watcher.done():
            try:
                await asyncio.wait_for(asyncio.shield(watcher), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            if not watcher.done():
                watcher.cancel()
                try:
                    await watcher
                except (asyncio.CancelledError, Exception):
                    pass

        logger.debug('Shell process %s killed', process_id)
        return record

    # ── Shared: Query ─────────────────────────────────────────────────────────

    def get(self, process_id: str) -> ProcessRecord | None:
        return self._records.get(process_id)

    def list(self, status: ProcessStatus | None = None) -> list[ProcessRecord]:
        records = list(self._records.values())
        if status is not None:
            records = [r for r in records if r.status == status]
        return records

    def read_output(self, process_id: str, max_bytes: int = 12000) -> str:
        record = self._records.get(process_id)
        if record is None:
            raise KeyError(f"No process with id '{process_id}'")

        if record.type == 'agent':
            if record.output_file is None or not record.output_file.exists():
                return ''
            content = record.output_file.read_text(encoding='utf-8', errors='replace')
            return content[-max_bytes:] if len(content) > max_bytes else content

        # shell — in-memory ring buffer
        buf = self._buffers.get(process_id)
        if buf is None:
            raise KeyError(f"No buffer for process '{process_id}'")
        data = bytes(buf[-max_bytes:]) if len(buf) > max_bytes else bytes(buf)
        return data.decode('utf-8', errors='replace')

    # ── Completion listeners ──────────────────────────────────────────────────

    def register_completion_listener(self, listener: CompletionListener) -> Callable[[], None]:
        """Register a callback fired when any process reaches a terminal state.
        Returns an unregister callable."""
        lid = uuid4().hex
        self._completion_listeners[lid] = listener

        def _unregister() -> None:
            self._completion_listeners.pop(lid, None)

        return _unregister

    # ── Shell internals ───────────────────────────────────────────────────────

    async def _watch_shell(self, pid: str, proc: asyncio.subprocess.Process) -> None:
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
        if record.status == ProcessStatus.RUNNING:
            record.status = ProcessStatus.COMPLETED if return_code == 0 else ProcessStatus.FAILED
        self._subprocesses.pop(pid, None)
        logger.debug('Shell process %s exited with code %d', pid, return_code)
        await self._notify_listeners(record)

    async def _watch_adopted(self, pid: str, proc: asyncio.subprocess.Process) -> None:
        buf = self._buffers[pid]

        async def _drain(stream) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > _MAX_BUFFER_BYTES:
                    del buf[:len(buf) - _MAX_BUFFER_BYTES]

        await asyncio.gather(_drain(proc.stdout), _drain(proc.stderr))
        return_code = await proc.wait()
        record = self._records.get(pid)
        if record is None:
            return
        record.ended_at = time.time()
        record.return_code = return_code
        if record.status == ProcessStatus.RUNNING:
            record.status = ProcessStatus.COMPLETED if return_code == 0 else ProcessStatus.FAILED
        self._subprocesses.pop(pid, None)
        logger.debug('Adopted process %s exited with code %d', pid, return_code)
        await self._notify_listeners(record)

    # ── Agent internals ───────────────────────────────────────────────────────

    async def _run_agent_session(
        self,
        pid: str,
        provider: str,
        model: str,
        env: dict[str, str] | None,  # reserved for future env injection into acp serve
    ) -> None:
        from operator_use.acp.client import ACPClient

        record = self._records[pid]
        queue = self._agent_queues[pid]

        self._append_output(record, f"[Agent started | provider={provider} model={model}]\n\n")

        try:
            client = ACPClient.stdio('operator', 'acp', 'serve', '--provider', provider, '--model', model)
            async with client as c:
                async with c.session() as sid:
                    while True:
                        prompt = await queue.get()
                        if prompt is None:           # stop sentinel
                            break
                        self._append_output(record, f"[User]\n{prompt}\n\n")
                        try:
                            result = await c.run(prompt, sid)
                            self._append_output(record, f"[Agent]\n{result}\n\n")
                        except Exception as exc:
                            self._append_output(record, f"[Error]\n{exc}\n\n")
                            logger.warning('Agent process %s run error: %s', pid, exc)

            record.status = ProcessStatus.COMPLETED

        except asyncio.CancelledError:
            record.status = ProcessStatus.KILLED
        except Exception as exc:
            logger.error('Agent process %s session failed: %s', pid, exc)
            self._append_output(record, f"[Session error]\n{exc}\n")
            record.status = ProcessStatus.FAILED
        finally:
            record.ended_at = time.time()
            self._agent_queues.pop(pid, None)
            self._agent_session_tasks.pop(pid, None)
            logger.debug('Agent process %s finished | status=%s', pid, record.status)
            await self._notify_listeners(record)

    async def _stop_agent(self, process_id: str, record: ProcessRecord) -> ProcessRecord:
        queue = self._agent_queues.get(process_id)
        if queue is not None:
            await queue.put(None)           # stop sentinel

        session_task = self._agent_session_tasks.get(process_id)
        if session_task and not session_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(session_task), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                session_task.cancel()
                try:
                    await session_task
                except (asyncio.CancelledError, Exception):
                    pass

        record.status = ProcessStatus.KILLED
        record.ended_at = time.time()
        logger.debug('Agent process %s stopped', process_id)
        return record

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _append_output(self, record: ProcessRecord, text: str) -> None:
        if record.output_file is not None:
            with record.output_file.open('a', encoding='utf-8') as f:
                f.write(text)

    async def _notify_listeners(self, record: ProcessRecord) -> None:
        for lid, listener in list(self._completion_listeners.items()):
            try:
                result = listener(record)
                if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug('Completion listener %s failed for process %s', lid, record.id, exc_info=True)
        self._prune_terminated()

    def _prune_terminated(self) -> None:
        """Evict the oldest terminated processes (and their buffers) once more
        than _max_terminated have finished. Running processes are never evicted,
        and a process that finished within the grace window is never evicted —
        the agent polls to learn a process was killed and to read its output, so
        a recently-finished one must stay readable even if that briefly exceeds
        the cap."""
        if self._max_terminated <= 0:
            return
        terminal = [r for r in self._records.values() if r.status != ProcessStatus.RUNNING]
        excess = len(terminal) - self._max_terminated
        if excess <= 0:
            return
        cutoff = time.time() - self._terminated_grace_s
        evictable = [r for r in terminal if (r.ended_at or 0.0) < cutoff]
        evictable.sort(key=lambda r: r.ended_at or 0.0)
        for r in evictable[:excess]:
            self._records.pop(r.id, None)
            self._buffers.pop(r.id, None)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Kill all running processes on shutdown."""
        for pid in list(self._subprocesses.keys()):
            try:
                await self.stop(pid)
            except Exception:
                logger.debug('Error stopping shell process %s on close', pid, exc_info=True)

        for pid in list(self._agent_session_tasks.keys()):
            try:
                await self._stop_agent(pid, self._records[pid])
            except Exception:
                logger.debug('Error stopping agent process %s on close', pid, exc_info=True)
