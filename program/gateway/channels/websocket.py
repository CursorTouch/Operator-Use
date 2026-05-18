from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel, GatewayEvent

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
        {"type": "stream_start"}
        {"type": "chunk", "text": "...", "kind": "text"|"thinking"}
        {"type": "stream_end"}
        {"type": "tool_start", "name": "...", "args": {...}}
        {"type": "tool_end",   "name": "...", "result": "...", "is_error": false}
        {"type": "error",      "message": "..."}
        {"type": "done"}
        {"type": "message",    "text": "..."}   ← out-of-band send()

    Client → server JSON protocol:
        {"type": "message", "text": "..."}
    """

    def __init__(self, connection: ServerConnection, gateway: Gateway) -> None:
        if not _WS_AVAILABLE:
            raise ImportError("websockets package is required for WebSocketChannel.")
        self._conn = connection
        self._gateway = gateway
        self._id = f"ws:{id(connection)}"

    @property
    def channel_id(self) -> str:
        return self._id

    async def start(self) -> None:
        """Register with the gateway and process incoming messages until the connection closes."""
        self._gateway.register(self)
        try:
            async for raw in self._conn:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._conn.send(json.dumps({'type': 'error', 'message': 'Invalid JSON.'}))
                    continue

                if msg.get('type') == 'message':
                    text = str(msg.get('text', '')).strip()
                    if text:
                        await self._gateway.send(self._id, text)
                else:
                    logger.debug("WebSocketChannel %r: unknown message type %r", self._id, msg.get('type'))
        finally:
            self._gateway.unregister(self._id)

    async def on_event(self, event: GatewayEvent) -> None:
        payload = {'type': event.type, **event.data}
        try:
            await self._conn.send(json.dumps(payload))
        except Exception:
            logger.warning("WebSocketChannel %r: send failed for event %r", self._id, event.type)

    async def send(self, text: str) -> None:
        try:
            await self._conn.send(json.dumps({'type': 'message', 'text': text}))
        except Exception:
            logger.warning("WebSocketChannel %r: send failed", self._id)


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
            await channel.start()

        async with ws_serve(_handler, self._host, self._port):
            logger.info("WebSocket gateway listening on ws://%s:%d", self._host, self._port)
            await asyncio.Future()  # run until cancelled
