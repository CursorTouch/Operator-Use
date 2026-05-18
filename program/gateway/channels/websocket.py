from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart, text_from_parts

if TYPE_CHECKING:
    from program.gateway.service import Gateway

logger = logging.getLogger(__name__)

try:
    from websockets.asyncio.server import ServerConnection, serve as ws_serve
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class WebSocketChannel(BaseChannel):
    """
    WebSocket channel — one instance per connected client.

    Server → client JSON protocol:
        {"type": "start"}
        {"type": "chunk", "text": "...", "kind": "text"|"thinking"}
        {"type": "chunk", "kind": "tool_start", "name": "...", "args": {...}}
        {"type": "chunk", "kind": "tool_end", "is_error": true, "result": "..."}
        {"type": "end"}
        {"type": "done"}
        {"type": "error", "text": "..."}
        {"type": "message", "text": "..."}   ← out-of-band send()

    Client → server JSON protocol:
        {"type": "message", "text": "..."}
    """

    def __init__(self, connection: ServerConnection, gateway: Gateway) -> None:
        super().__init__()
        if not _WS_AVAILABLE:
            raise ImportError("websockets package is required for WebSocketChannel.")
        self._conn = connection
        self._gateway = gateway
        self._conn_id = f"ws:{id(connection)}"

    @property
    def channel_id(self) -> str:
        return self._conn_id

    async def connect(self) -> None:
        """Register with gateway, process incoming messages until connection closes."""
        self._gateway.register(self)
        try:
            async for raw in self._conn:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._conn.send(json.dumps({'type': 'error', 'text': 'Invalid JSON.'}))
                    continue

                if msg.get('type') == 'message':
                    text = str(msg.get('text', '')).strip()
                    if text:
                        incoming = IncomingMessage(
                            channel=self._conn_id,
                            chat_id=self._conn_id,
                            parts=[TextPart(text)],
                        )
                        await self.receive(incoming)
                else:
                    logger.debug(
                        "WebSocketChannel %r: unknown message type %r",
                        self._conn_id, msg.get('type'),
                    )
        finally:
            self._gateway.unregister(self._conn_id)

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        try:
            await self._conn.close()
        except Exception:
            logger.exception("WebSocketChannel: error during disconnect")

    async def send(self, msg: OutgoingMessage) -> None:
        """Send an outgoing message as JSON to the WebSocket client."""
        phase = msg.stream_phase
        metadata = msg.metadata

        if phase == StreamPhase.START:
            payload = {'type': 'start'}
        elif phase == StreamPhase.CHUNK:
            text = text_from_parts(msg.parts)
            payload = {'type': 'chunk', **metadata}
            if text:
                payload['text'] = text
        elif phase == StreamPhase.END:
            payload = {'type': 'end'}
        elif phase == StreamPhase.DONE:
            payload = {'type': 'done'}
        elif phase == StreamPhase.ERROR:
            text = text_from_parts(msg.parts) or "Unknown error"
            payload = {'type': 'error', 'text': text}
        else:
            # Direct/out-of-band send
            text = text_from_parts(msg.parts)
            payload = {'type': 'message', 'text': text}

        try:
            await self._conn.send(json.dumps(payload))
        except Exception:
            logger.warning("WebSocketChannel %r: send failed", self._conn_id)


class WebSocketServer:
    """
    Listens for WebSocket connections and spawns a WebSocketChannel per client.

    Usage:
        server = WebSocketServer(gateway, host='127.0.0.1', port=8765)
        await server.start()   # run as a background task or directly
    """

    def __init__(
        self,
        gateway: Gateway,
        host: str = '127.0.0.1',
        port: int = 8765,
    ) -> None:
        if not _WS_AVAILABLE:
            raise ImportError("websockets package is required for WebSocket support.")
        self._gateway = gateway
        self._host = host
        self._port = port

    async def start(self) -> None:
        """Accept connections until the asyncio task is cancelled."""
        async def _handler(connection: ServerConnection) -> None:
            channel = WebSocketChannel(connection, self._gateway)
            await channel.connect()

        async with ws_serve(_handler, self._host, self._port):
            logger.info("WebSocket gateway listening on ws://%s:%d", self._host, self._port)
            await asyncio.Future()  # run until cancelled
