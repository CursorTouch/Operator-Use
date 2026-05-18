from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import TYPE_CHECKING, Callable, AsyncIterator

from program.acp.types import AgentMetadata

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

AgentRunnerFn = Callable[[str, str | None], AsyncIterator[str]]

_PARSE_ERROR    = -32700
_INVALID_REQ    = -32600
_METHOD_NF      = -32601
_INTERNAL_ERR   = -32603


class ACPStdioServer:
    """
    JSON-RPC 2.0 over stdin/stdout — for IDE and CLI integrations.

    Stdout carries only JSON-RPC messages. All logging is redirected to
    stderr so it never corrupts the protocol stream.

    Methods:
        initialize          → AgentMetadata
        agent/run           → {"text": "<full response>"}
        agent/stream        → {"done": true}   (chunks sent as notifications)

    Notifications (server → client, no id):
        agent/chunk         → {"request_id": N, "text": "...", "done": false|true}
    """

    def __init__(
        self,
        metadata: AgentMetadata,
        runner: AgentRunnerFn,
    ) -> None:
        self._metadata = metadata
        self._runner   = runner
        self._running  = False

    async def serve(self) -> None:
        """Read JSON-RPC requests from stdin until EOF."""
        self._running = True
        loop = asyncio.get_running_loop()

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
            except Exception:
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._send_error(None, _PARSE_ERROR, 'Parse error')
                continue
            asyncio.create_task(self._dispatch(msg))

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch(self, msg: dict) -> None:
        req_id = msg.get('id')
        method = msg.get('method', '')
        params = msg.get('params') or {}

        if msg.get('jsonrpc') != '2.0':
            self._send_error(req_id, _INVALID_REQ, 'Invalid JSON-RPC version')
            return

        match method:
            case 'initialize':
                self._send_result(req_id, self._metadata.model_dump())

            case 'agent/run':
                text       = params.get('text', '')
                session_id = params.get('session_id')
                full_text  = await self._collect(text, session_id)
                self._send_result(req_id, {'text': full_text})

            case 'agent/stream':
                text       = params.get('text', '')
                session_id = params.get('session_id')
                await self._stream(req_id, text, session_id)
                self._send_result(req_id, {'done': True})

            case _:
                self._send_error(req_id, _METHOD_NF, f'Method not found: {method!r}')

    # ── Runner helpers ────────────────────────────────────────────────────────

    async def _collect(self, text: str, session_id: str | None) -> str:
        chunks: list[str] = []
        try:
            async for chunk in self._runner(text, session_id):
                chunks.append(chunk)
        except Exception as exc:
            self._send_error(None, _INTERNAL_ERR, str(exc))
        return ''.join(chunks)

    async def _stream(self, req_id, text: str, session_id: str | None) -> None:
        try:
            async for chunk in self._runner(text, session_id):
                self._send_notification('agent/chunk', {
                    'request_id': req_id,
                    'text': chunk,
                    'done': False,
                })
            self._send_notification('agent/chunk', {
                'request_id': req_id,
                'text': '',
                'done': True,
            })
        except Exception as exc:
            self._send_error(req_id, _INTERNAL_ERR, str(exc))

    # ── Wire helpers ──────────────────────────────────────────────────────────

    def _write(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg) + '\n')
        sys.stdout.flush()

    def _send_result(self, req_id, result) -> None:
        self._write({'jsonrpc': '2.0', 'id': req_id, 'result': result})

    def _send_error(self, req_id, code: int, message: str) -> None:
        self._write({'jsonrpc': '2.0', 'id': req_id, 'error': {'code': code, 'message': message}})

    def _send_notification(self, method: str, params: dict) -> None:
        self._write({'jsonrpc': '2.0', 'method': method, 'params': params})
