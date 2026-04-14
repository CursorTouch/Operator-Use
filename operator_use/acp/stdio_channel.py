"""JSON-RPC 2.0 over stdio channel for Operator.

Lets IDEs and CLI tools (Claude Code, Zed, Codex, OpenCode, Gemini CLI…)
connect to Operator by piping newline-delimited JSON-RPC 2.0 over stdin/stdout
— no HTTP server required.

Protocol
--------
All messages are newline-delimited JSON (one object per line).

**Client → Operator (requests):**

    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    {"jsonrpc": "2.0", "id": 2, "method": "agent/run",    "params": {"text": "...", "session_id": "s1"}}
    {"jsonrpc": "2.0", "id": 3, "method": "agent/stream", "params": {"text": "...", "session_id": "s1"}}

**Operator → Client (responses):**

    {"jsonrpc": "2.0", "id": 1, "result": {"agent_id": "operator", "name": "Operator", ...}}
    {"jsonrpc": "2.0", "id": 2, "result": {"text": "full response"}}
    {"jsonrpc": "2.0", "id": 3, "result": {"done": true}}

**Streaming notifications** (sent *before* the final result, no "id" field):

    {"jsonrpc": "2.0", "method": "agent/chunk", "params": {"request_id": 3, "text": "partial...", "done": false}}
    {"jsonrpc": "2.0", "method": "agent/chunk", "params": {"request_id": 3, "text": "last bit",  "done": true}}

**Error responses:**

    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}

JSON-RPC error codes
--------------------
-32700  Parse error
-32600  Invalid Request
-32601  Method not found
-32603  Internal error
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from operator_use.acp.config import ACPStdioConfig
from operator_use.bus.views import (
    IncomingMessage,
    OutgoingMessage,
    StreamPhase,
    TextPart,
    text_from_parts,
)
from operator_use.gateway.channels.base import BaseChannel

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 error codes
_ERR_PARSE = -32700
_ERR_INVALID = -32600
_ERR_NOT_FOUND = -32601
_ERR_INTERNAL = -32603


class ACPStdioChannel(BaseChannel):
    """JSON-RPC 2.0 over stdio gateway channel.

    Reads requests from stdin, writes responses (and streaming notifications)
    to stdout.  All logging is redirected to stderr so stdout stays clean.
    """

    def __init__(self, config: ACPStdioConfig, bus=None) -> None:
        super().__init__(config, bus)
        # run_id → asyncio.Queue[str | None]
        self._response_queues: dict[str, asyncio.Queue] = {}
        # request_id (JSON-RPC) → run_id (internal)
        self._req_to_run: dict[Any, str] = {}
        # request_id → is_streaming (bool)
        self._req_streaming: dict[Any, bool] = {}
        # Serialise all stdout writes
        self._stdout_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # BaseChannel identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "stdio"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("ACP stdio channel disabled, skipping")
            return

        if self.config.redirect_logging_to_stderr:
            _redirect_logging_to_stderr()

        self.running = True
        await self._listen()

    async def stop(self) -> None:
        self.running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        for q in self._response_queues.values():
            await q.put(None)
        self._response_queues.clear()
        self._req_to_run.clear()
        self._req_streaming.clear()

    async def _listen(self) -> None:
        """Spawn background task that reads JSON-RPC lines from stdin."""
        self._reader_task = asyncio.ensure_future(self._reader_loop())

    # ------------------------------------------------------------------
    # Stdin reader loop
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                line: str = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception as exc:
                logger.debug(f"stdio read error: {exc}")
                break

            if not line:
                # EOF — stdin closed
                break

            line = line.strip()
            if not line:
                continue

            asyncio.ensure_future(self._handle_line(line))

    async def _handle_line(self, line: str) -> None:
        req_id: Any = None
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            await self._write_error(None, _ERR_PARSE, f"Parse error: {exc}")
            return

        if not isinstance(req, dict):
            await self._write_error(None, _ERR_INVALID, "Request must be a JSON object")
            return

        req_id = req.get("id")
        method = req.get("method", "")

        if req.get("jsonrpc") != "2.0":
            await self._write_error(req_id, _ERR_INVALID, "jsonrpc must be '2.0'")
            return

        params: dict = req.get("params") or {}

        if method == "initialize":
            await self._handle_initialize(req_id)
        elif method == "agent/run":
            await self._handle_run(req_id, params, streaming=False)
        elif method == "agent/stream":
            await self._handle_run(req_id, params, streaming=True)
        else:
            await self._write_error(req_id, _ERR_NOT_FOUND, f"Method not found: {method!r}")

    # ------------------------------------------------------------------
    # Method handlers
    # ------------------------------------------------------------------

    async def _handle_initialize(self, req_id: Any) -> None:
        await self._write_result(
            req_id,
            {
                "agent_id": self.config.agent_id,
                "name": self.config.agent_name,
                "description": self.config.agent_description,
                "protocol": "jsonrpc/2.0",
                "capabilities": {
                    "methods": ["initialize", "agent/run", "agent/stream"],
                    "streaming": True,
                },
            },
        )

    async def _handle_run(self, req_id: Any, params: dict, *, streaming: bool) -> None:
        text: str = params.get("text", "")
        session_id: str | None = params.get("session_id") or None

        if not text:
            await self._write_error(req_id, _ERR_INVALID, "params.text is required")
            return

        run_id = session_id or str(uuid.uuid4())
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._response_queues[run_id] = queue
        self._req_to_run[req_id] = run_id
        self._req_streaming[req_id] = streaming

        incoming = IncomingMessage(
            channel=self.name,
            chat_id=run_id,
            parts=[TextPart(content=text)],
            user_id=run_id,
            metadata={
                "jsonrpc_request_id": req_id,
                "session_id": session_id,
                "run_id": run_id,
                "streaming": streaming,
            },
        )
        await self.receive(incoming)

        # Collect chunks and send notifications / final result
        try:
            full_text_parts: list[str] = []
            while True:
                chunk = await asyncio.wait_for(queue.get(), timeout=self.config.timeout)
                if chunk is None:
                    break
                full_text_parts.append(chunk)
                if streaming:
                    await self._write_notification(
                        "agent/chunk",
                        {
                            "request_id": req_id,
                            "text": chunk,
                            "done": False,
                        },
                    )

            # Final notification mark (streaming)
            if streaming and full_text_parts:
                await self._write_notification(
                    "agent/chunk",
                    {
                        "request_id": req_id,
                        "text": "",
                        "done": True,
                    },
                )
                await self._write_result(req_id, {"done": True})
            else:
                # Non-streaming: send full accumulated text
                await self._write_result(req_id, {"text": "".join(full_text_parts)})

        except asyncio.TimeoutError:
            await self._write_error(req_id, _ERR_INTERNAL, "Agent response timed out")
        finally:
            self._response_queues.pop(run_id, None)
            self._req_to_run.pop(req_id, None)
            self._req_streaming.pop(req_id, None)

    # ------------------------------------------------------------------
    # Outgoing: agent → stdio client
    # ------------------------------------------------------------------

    async def send(self, message: OutgoingMessage) -> int | None:
        """Receive agent output and forward to the waiting request queue."""
        run_id = message.chat_id
        queue = self._response_queues.get(run_id)
        if not queue:
            logger.debug(f"stdio: no queue for run {run_id}, dropping")
            return None

        phase = message.stream_phase

        if phase in (StreamPhase.CHUNK, StreamPhase.END, None):
            text = text_from_parts(message.parts or [])
            if text:
                await queue.put(text)

        if phase in (StreamPhase.END, StreamPhase.DONE) or phase is None:
            await queue.put(None)

        return None

    # ------------------------------------------------------------------
    # Stdout writers
    # ------------------------------------------------------------------

    async def _write(self, obj: dict) -> None:
        async with self._stdout_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    async def _write_result(self, req_id: Any, result: Any) -> None:
        await self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _write_error(self, req_id: Any, code: int, message: str) -> None:
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": code, "message": message},
            }
        )

    async def _write_notification(self, method: str, params: Any) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})


# ------------------------------------------------------------------
# Logging helper
# ------------------------------------------------------------------


def _redirect_logging_to_stderr() -> None:
    """Point all logging handlers at stderr so stdout stays clean JSON-RPC."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.stream = sys.stderr
    # Ensure at least one stderr handler exists
    if not any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in root.handlers
    ):
        root.addHandler(logging.StreamHandler(sys.stderr))
