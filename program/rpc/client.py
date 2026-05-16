from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable


class RPCClient:
    """
    Spawn the RPC server as a subprocess and communicate over JSONL stdio.

    Usage:
        client = RpcClient(cwd="/my/project")
        await client.start()

        events = await client.prompt_and_wait("Write a hello-world script")
        for event in events:
            print(event)

        await client.stop()

    All send_*() methods are fire-and-forget (return when the ack arrives).
    Use collect_events() or prompt_and_wait() to also capture streamed events.
    """

    def __init__(
        self,
        cwd: str | Path = ".",
        model_id: str | None = None,
        provider: str | None = None,
        python: str = sys.executable,
        timeout: float = 30.0,
    ) -> None:
        self._cwd = Path(cwd).resolve()
        self._model_id = model_id
        self._provider = provider
        self._python = python
        self._timeout = timeout

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._listeners: list[Callable[[dict], None]] = []
        self._req_counter = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        args = [self._python, '-m', 'program.rpc', '--cwd', str(self._cwd)]
        if self._model_id:
            args += ['--model', self._model_id]
        if self._provider:
            args += ['--provider', self._provider]

        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.close()         # EOF → server shuts down
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._reader_task:
            self._reader_task.cancel()

    async def __aenter__(self) -> RPCClient:
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Sending commands
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._req_counter += 1
        return f'req_{self._req_counter}'

    async def _send(self, obj: dict) -> dict:
        req_id = self._next_id()
        obj['id'] = req_id

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        line = (json.dumps(obj) + '\n').encode('utf-8')
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"No response for {obj.get('type')!r} within {self._timeout}s")

    async def _fire(self, obj: dict) -> None:
        """Send without waiting for a response (no id)."""
        line = (json.dumps(obj) + '\n').encode('utf-8')
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def prompt(self, message: str) -> dict:
        return await self._send({'type': 'prompt', 'message': message})

    async def steer(self, message: str) -> dict:
        return await self._send({'type': 'steer', 'message': message})

    async def follow_up(self, message: str) -> dict:
        return await self._send({'type': 'follow_up', 'message': message})

    async def abort(self) -> dict:
        return await self._send({'type': 'abort'})

    async def get_state(self) -> dict:
        resp = await self._send({'type': 'get_state'})
        return resp.get('data', {})

    async def get_messages(self) -> list:
        resp = await self._send({'type': 'get_messages'})
        return resp.get('data', {}).get('messages', [])

    async def get_last_assistant_text(self) -> str | None:
        resp = await self._send({'type': 'get_last_assistant_text'})
        return resp.get('data', {}).get('text')

    async def new_session(self) -> dict:
        return await self._send({'type': 'new_session'})

    async def switch_session(self, session_path: str | Path) -> dict:
        return await self._send({'type': 'switch_session', 'session_path': str(session_path)})

    async def fork(self, entry_id: str) -> dict:
        return await self._send({'type': 'fork', 'entry_id': entry_id})

    async def set_session_name(self, name: str) -> dict:
        return await self._send({'type': 'set_session_name', 'name': name})

    async def get_session_stats(self) -> dict:
        resp = await self._send({'type': 'get_session_stats'})
        return resp.get('data', {})

    async def set_model(self, model_id: str, provider: str | None = None) -> dict:
        cmd: dict = {'type': 'set_model', 'model_id': model_id}
        if provider:
            cmd['provider'] = provider
        return await self._send(cmd)

    async def set_thinking_level(self, level: str) -> dict:
        return await self._send({'type': 'set_thinking_level', 'level': level})

    async def compact(self, custom_instructions: str | None = None) -> dict:
        cmd: dict = {'type': 'compact'}
        if custom_instructions:
            cmd['custom_instructions'] = custom_instructions
        return await self._send(cmd)

    async def set_auto_compaction(self, enabled: bool) -> dict:
        return await self._send({'type': 'set_auto_compaction', 'enabled': enabled})

    async def extension_ui_response(
        self,
        request_id: str,
        value: Any = None,
        cancelled: bool = False,
    ) -> None:
        await self._fire({
            'type': 'extension_ui_response',
            'id': request_id,
            'value': value,
            'cancelled': cancelled,
        })

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def on_event(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        """Register a listener for streamed events. Returns an unsubscribe callable."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    async def wait_for_idle(self, timeout: float = 120.0) -> None:
        """Block until an 'agent_end' or 'settled' event arrives."""
        done: asyncio.Future = asyncio.get_event_loop().create_future()

        def listener(event: dict) -> None:
            if event.get('type') in ('agent_end', 'settled') and not done.done():
                done.set_result(event)

        unsub = self.on_event(listener)
        try:
            await asyncio.wait_for(asyncio.shield(done), timeout=timeout)
        finally:
            unsub()

    async def collect_events(self, timeout: float = 120.0) -> list[dict]:
        """Collect all events until 'agent_end' or 'settled'."""
        events: list[dict] = []
        done: asyncio.Future = asyncio.get_event_loop().create_future()

        def listener(event: dict) -> None:
            events.append(event)
            if event.get('type') in ('agent_end', 'settled') and not done.done():
                done.set_result(None)

        unsub = self.on_event(listener)
        try:
            await asyncio.wait_for(asyncio.shield(done), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            unsub()

        return events

    async def prompt_and_wait(
        self,
        message: str,
        timeout: float = 120.0,
    ) -> list[dict]:
        """Send a prompt and collect all events until the agent settles."""
        events: list[dict] = []
        done: asyncio.Future = asyncio.get_event_loop().create_future()

        def listener(event: dict) -> None:
            events.append(event)
            if event.get('type') in ('agent_end', 'settled') and not done.done():
                done.set_result(None)

        unsub = self.on_event(listener)
        try:
            await self.prompt(message)
            await asyncio.wait_for(asyncio.shield(done), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            unsub()

        return events

    # ------------------------------------------------------------------
    # Internal reader
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            line = raw.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            try:
                data: dict = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get('type') == 'response':
                req_id = data.get('id')
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if data.get('success'):
                            fut.set_result(data)
                        else:
                            fut.set_exception(RuntimeError(data.get('error', 'RPC error')))
            else:
                for listener in list(self._listeners):
                    try:
                        listener(data)
                    except Exception:
                        pass
