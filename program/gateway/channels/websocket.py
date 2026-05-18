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

    async def on_event(self, event: GatewayEvent) -> None:
        payload = {'type': event.type, **event.data}
        try:
            await self._conn.send(json.dumps(payload))
        except Exception:
            logger.warning("WebSocketChannel %r: send failed for event %r", self._id, event.type)

    async def serve(self) -> None:
        """Process incoming messages until the connection closes."""
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


async def serve_websocket(
    gateway: Gateway,
    host: str = '127.0.0.1',
    port: int = 8765,
) -> None:
    """
    Start a WebSocket server.  Each connection becomes a WebSocketChannel.

    Runs until the task is cancelled.

    Usage:
        asyncio.create_task(serve_websocket(gateway, port=8765))
    """
    if not _WS_AVAILABLE:
        raise ImportError("websockets package is required for WebSocket support.")

    async def _handler(connection: ServerConnection) -> None:
        channel = WebSocketChannel(connection, gateway)
        await channel.serve()

    async with ws_serve(_handler, host, port):
        logger.info("WebSocket gateway listening on ws://%s:%d", host, port)
        await asyncio.Future()  # run until cancelled
