from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from program.runtime.runtime import AgentSessionRuntime
from program.runtime.types import PromptOptions
from program.engine.types import AgentEventType


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    """Recursively convert an object to a JSON-serialisable structure."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            k: _to_jsonable(v)
            for k, v in dataclasses.asdict(obj).items()
        }
    if hasattr(obj, 'model_dump'):          # Pydantic model
        return {k: _to_jsonable(v) for k, v in obj.model_dump().items()}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return str(obj)


def _serialize(obj: Any) -> str:
    return json.dumps(_to_jsonable(obj), ensure_ascii=False) + '\n'


# ---------------------------------------------------------------------------
# RPC server
# ---------------------------------------------------------------------------

class RPCServer:
    """
    Bidirectional JSONL RPC server.

    Commands flow in on stdin; responses and events flow out on stdout.
    Every line is a self-contained JSON object terminated by a single LF.

    Command shape:  { "type": "<cmd>", "id": "<optional>", ...fields }
    Response shape: { "type": "response", "command": "<cmd>", "success": bool,
                      "id": "<optional>", "data": {...} | "error": "..." }
    Events:         { "type": "<event_type>", ...fields }

    Extension UI sub-protocol (blocking round-trips):
      Agent → Host: { "type": "extension_ui_request", "id": "<uuid>",
                      "method": "confirm|select|input|notify", ...fields }
      Host → Agent: { "type": "extension_ui_response", "id": "<uuid>",
                      "value": ..., "cancelled": bool }
    """

    def __init__(self, runtime: AgentSessionRuntime) -> None:
        self._runtime = runtime
        self._lock = asyncio.Lock()
        self._pending_ui: dict[str, asyncio.Future] = {}
        self._unsub_hooks: Callable | None = None
        self._shutdown = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._bind()

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        while not self._shutdown:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            if line:
                asyncio.create_task(self._handle_line(line))

        self._close()

    # ------------------------------------------------------------------
    # Event subscription / rebind
    # ------------------------------------------------------------------

    def _bind(self) -> None:
        self._close()
        session = self._runtime.current_session
        if session is None:
            return

        async def _on_event(event: Any) -> None:
            d = _to_jsonable(event)
            if not isinstance(d, dict):
                d = {'data': d}
            if 'type' not in d:
                raw_type = getattr(event, 'type', 'unknown')
                d['type'] = raw_type.value if hasattr(raw_type, 'value') else raw_type
            await self._write(d)

        # Single subscription covers both engine events (via AgentLoop → Hooks)
        # and extension/session events (via ExtensionRuntime → Hooks).
        self._unsub_hooks = session.hooks.subscribe(_on_event)

    def _close(self) -> None:
        if self._unsub_hooks:
            try:
                self._unsub_hooks()
            except Exception:
                pass
            self._unsub_hooks = None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def _write(self, obj: Any) -> None:
        async with self._lock:
            try:
                line = _serialize(obj)
                sys.stdout.buffer.write(line.encode('utf-8'))
                sys.stdout.buffer.flush()
            except Exception:
                pass

    async def _ok(self, cmd_id: str | None, command: str, data: Any = None) -> None:
        msg: dict = {'type': 'response', 'command': command, 'success': True}
        if cmd_id:
            msg['id'] = cmd_id
        if data is not None:
            msg['data'] = _to_jsonable(data)
        await self._write(msg)

    async def _err(self, cmd_id: str | None, command: str, error: str) -> None:
        msg: dict = {'type': 'response', 'command': command, 'success': False, 'error': error}
        if cmd_id:
            msg['id'] = cmd_id
        await self._write(msg)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _handle_line(self, line: str) -> None:
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as exc:
            await self._err(None, 'parse', str(exc))
            return

        cmd_id: str | None = cmd.get('id')
        cmd_type: str = cmd.get('type', '')

        # Extension UI response — handled separately
        if cmd_type == 'extension_ui_response':
            fut = self._pending_ui.pop(cmd.get('id', ''), None)
            if fut and not fut.done():
                fut.set_result(cmd)
            return

        try:
            await self._dispatch(cmd_type, cmd_id, cmd)
        except Exception as exc:
            await self._err(cmd_id, cmd_type or 'unknown', str(exc))

    async def _dispatch(self, cmd_type: str, cmd_id: str | None, cmd: dict) -> None:  # noqa: C901
        session = self._runtime.current_session

        match cmd_type:

            # ── Prompting ────────────────────────────────────────────────
            case 'prompt':
                message = cmd.get('message', '')
                if not message:
                    await self._err(cmd_id, 'prompt', "'message' is required")
                    return
                await self._ok(cmd_id, 'prompt')   # ack before async work
                asyncio.create_task(
                    self._runtime.handle_input(message, PromptOptions(source='rpc'))
                )

            case 'steer':
                message = cmd.get('message', '')
                if not message or session is None:
                    await self._err(cmd_id, 'steer', "'message' required and session must be active")
                    return
                from program.message.types import UserMessage, TextContent
                await session._loop.steer(UserMessage(contents=[TextContent(content=message)]))
                await self._ok(cmd_id, 'steer')

            case 'follow_up':
                message = cmd.get('message', '')
                if not message or session is None:
                    await self._err(cmd_id, 'follow_up', "'message' required and session must be active")
                    return
                from program.message.types import UserMessage, TextContent
                await session._loop.follow_up(UserMessage(contents=[TextContent(content=message)]))
                await self._ok(cmd_id, 'follow_up')

            case 'abort':
                if session:
                    session.abort()
                await self._ok(cmd_id, 'abort')

            # ── Session management ───────────────────────────────────────
            case 'new_session':
                await self._runtime.new_session()
                self._bind()
                await self._ok(cmd_id, 'new_session', {'cancelled': False})

            case 'switch_session':
                path_str = cmd.get('session_path')
                if not path_str:
                    await self._err(cmd_id, 'switch_session', "'session_path' is required")
                    return
                await self._runtime.resume_session(Path(path_str))
                self._bind()
                await self._ok(cmd_id, 'switch_session', {'cancelled': False})

            case 'fork':
                entry_id = cmd.get('entry_id')
                if not entry_id:
                    await self._err(cmd_id, 'fork', "'entry_id' is required")
                    return
                await self._runtime.fork_session(entry_id)
                self._bind()
                await self._ok(cmd_id, 'fork', {'entry_id': entry_id, 'cancelled': False})

            case 'set_session_name':
                name = (cmd.get('name') or '').strip()
                if not name or session is None:
                    await self._err(cmd_id, 'set_session_name', "'name' is required")
                    return
                session._session.append_session_info(name)
                await self._ok(cmd_id, 'set_session_name')

            # ── State queries ────────────────────────────────────────────
            case 'get_state':
                await self._ok(cmd_id, 'get_state', self._build_state())

            case 'get_messages':
                if session is None:
                    await self._ok(cmd_id, 'get_messages', {'messages': []})
                    return
                msgs = session._loop.state.messages
                await self._ok(cmd_id, 'get_messages', {'messages': msgs})

            case 'get_last_assistant_text':
                text = self._last_assistant_text(session)
                await self._ok(cmd_id, 'get_last_assistant_text', {'text': text})

            case 'get_session_stats':
                await self._ok(cmd_id, 'get_session_stats', self._build_stats(session))

            # ── Model / thinking ─────────────────────────────────────────
            case 'set_model':
                model_id = cmd.get('model_id')
                if not model_id:
                    await self._err(cmd_id, 'set_model', "'model_id' is required")
                    return
                await self._ok(cmd_id, 'set_model', {'model_id': model_id})

            case 'set_thinking_level':
                level = cmd.get('level')
                if level is None or session is None:
                    await self._err(cmd_id, 'set_thinking_level', "'level' required and session must be active")
                    return
                from program.inference.types import ThinkingLevel
                try:
                    tl = ThinkingLevel(level)
                except ValueError:
                    await self._err(cmd_id, 'set_thinking_level', f"Unknown thinking level: {level!r}")
                    return
                session._session.append_thinking_level_change(tl)
                await self._ok(cmd_id, 'set_thinking_level', {'level': tl.value})

            # ── Compaction ───────────────────────────────────────────────
            case 'compact':
                if session is None:
                    await self._err(cmd_id, 'compact', "No active session")
                    return
                custom = cmd.get('custom_instructions')
                from program.extension.types import CompactOptions
                session.compact(CompactOptions(custom_instructions=custom))
                await self._ok(cmd_id, 'compact')

            case 'set_auto_compaction':
                enabled = cmd.get('enabled')
                if enabled is None or session is None:
                    await self._err(cmd_id, 'set_auto_compaction', "'enabled' required")
                    return
                session._compaction.settings.enabled = bool(enabled)
                await self._ok(cmd_id, 'set_auto_compaction', {'enabled': bool(enabled)})

            # ── Extension UI responses (handled above, just in case) ──────
            case 'extension_ui_response':
                pass

            case _:
                await self._err(cmd_id, cmd_type, f"Unknown command: {cmd_type!r}")

    # ------------------------------------------------------------------
    # Extension UI sub-protocol
    # ------------------------------------------------------------------

    async def extension_ui_request(
        self,
        request_id: str,
        method: str,
        timeout: float | None = None,
        **fields,
    ) -> dict | None:
        """
        Send a UI request to the host and await the response.
        Returns the response dict, or None on timeout/cancellation.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_ui[request_id] = fut

        await self._write({'type': 'extension_ui_request', 'id': request_id, 'method': method, **fields})

        try:
            if timeout is not None:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            return await fut
        except asyncio.TimeoutError:
            self._pending_ui.pop(request_id, None)
            return None
        except asyncio.CancelledError:
            self._pending_ui.pop(request_id, None)
            return None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _build_state(self) -> dict:
        session = self._runtime.current_session
        sm = self._runtime.session_manager
        return {
            'phase': session._phase if session else 'idle',
            'is_streaming': not session.is_idle() if session else False,
            'session_file': str(sm.session_file) if sm and sm.session_file else None,
            'session_id': sm.session_id if sm else None,
            'session_name': sm.get_session_name() if sm else None,
            'context_tokens': session._context_tokens if session else 0,
            'context_window': session._context_window if session else 0,
            'message_count': len(session._loop.state.messages) if session else 0,
        }

    def _build_stats(self, session) -> dict:
        if session is None:
            return {}
        sm = session._session
        entries = sm.get_entries()
        return {
            'session_id': sm.session_id,
            'session_name': sm.get_session_name(),
            'entry_count': len(entries),
            'context_tokens': session._context_tokens,
            'context_window': session._context_window,
        }

    def _last_assistant_text(self, session) -> str | None:
        if session is None:
            return None
        from program.message.types import AssistantMessage, TextContent
        for msg in reversed(session._loop.state.messages):
            if isinstance(msg, AssistantMessage):
                for content in reversed(msg.contents):
                    if isinstance(content, TextContent):
                        return content.content
        return None

    def _shutdown_server(self) -> None:
        self._shutdown = True
        self._close()


async def run_rpc_server(runtime: AgentSessionRuntime) -> None:
    """Entry point: start the RPC server and block until stdin closes."""
    server = RPCServer(runtime)
    await server.run()
