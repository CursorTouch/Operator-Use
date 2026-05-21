from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.acp.device_flow import DeviceFlowManager
    from program.acp.server import OperatorACPAgent

logger = logging.getLogger(__name__)


class ACPHttpServer:
    """
    Serves OperatorACPAgent over HTTP so remote machines can connect.

    Endpoints:
        GET  /acp/events              — SSE stream (server→client messages)
        POST /acp/rpc/{conn_id}       — client→server messages (JSON-RPC)
        POST /acp/auth/device         — initiate device flow (get user_code)
        POST /acp/auth/token          — poll for access token
        POST /acp/auth/approve        — approve a pending user_code (admin only)

    Auth:
        All /acp/events and /acp/rpc/* requests require:
            Authorization: Bearer <token>
        Tokens are issued by the device flow and validated by DeviceFlowManager.
    """

    def __init__(self, agent: OperatorACPAgent, device_flow: DeviceFlowManager) -> None:
        self._agent = agent
        self._device_flow = device_flow
        # conn_id → bridge_writer (POST /rpc writes incoming messages here)
        self._connections: dict[str, asyncio.StreamWriter] = {}

    def routes(self) -> list[web.RouteDef]:
        return [
            web.get('/acp/events', self._handle_events),
            web.post('/acp/rpc/{conn_id}', self._handle_rpc),
            web.post('/acp/auth/device', self._handle_device_request),
            web.post('/acp/auth/token', self._handle_token_poll),
            web.post('/acp/auth/approve', self._handle_approve),
        ]

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _bearer_token(self, request: web.Request) -> str | None:
        auth = request.headers.get('Authorization', '')
        return auth[7:] if auth.startswith('Bearer ') else None

    def _require_auth(self, request: web.Request) -> None:
        token = self._bearer_token(request)
        if not token or not self._device_flow.validate_token(token):
            raise web.HTTPUnauthorized(
                text='Invalid or missing Bearer token',
                headers={'WWW-Authenticate': 'Bearer'},
            )

    # ── SSE connection ────────────────────────────────────────────────────────

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        """Open an SSE stream; one per remote client connection."""
        self._require_auth(request)

        conn_id = str(uuid.uuid4())

        # Socket pair: agent side ↔ bridge side
        # agent side  → AgentSideConnection (reads/writes JSON-RPC lines)
        # bridge side → HTTP bridge (POST body in, SSE events out)
        sock_agent, sock_bridge = socket.socketpair()
        sock_agent.setblocking(False)
        sock_bridge.setblocking(False)

        acp_reader, acp_writer = await asyncio.open_connection(sock=sock_agent)
        bridge_reader, bridge_writer = await asyncio.open_connection(sock=sock_bridge)

        # AgentSideConnection convention (same as ClientSideConnection):
        #   input_stream  = StreamWriter → agent writes outgoing msgs → bridge_reader
        #   output_stream = StreamReader → agent reads incoming msgs  ← bridge_writer
        from acp.agent.connection import AgentSideConnection  # type: ignore[import-untyped]
        conn = AgentSideConnection(self._agent, acp_writer, acp_reader)

        # POST /rpc writes incoming client messages to bridge_writer
        self._connections[conn_id] = bridge_writer

        sse_queue: asyncio.Queue[str | None] = asyncio.Queue()

        listen_task = asyncio.create_task(conn.listen(), name=f'acp-listen-{conn_id[:8]}')
        forward_task = asyncio.create_task(
            self._forward_to_queue(bridge_reader, sse_queue),
            name=f'acp-forward-{conn_id[:8]}',
        )

        resp = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'X-ACP-Connection-ID': conn_id,
        })
        await resp.prepare(request)

        logger.info('ACP HTTP: client connected conn_id=%s', conn_id)
        try:
            while True:
                event = await sse_queue.get()
                if event is None:
                    break
                await resp.write(f'data: {event}\n\n'.encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            logger.info('ACP HTTP: client disconnected conn_id=%s', conn_id)
            listen_task.cancel()
            forward_task.cancel()
            self._connections.pop(conn_id, None)
            for writer in (acp_writer, bridge_writer):
                try:
                    writer.close()
                except Exception:
                    pass

        return resp

    async def _forward_to_queue(
        self,
        reader: asyncio.StreamReader,
        queue: asyncio.Queue[str | None],
    ) -> None:
        """Read JSON-RPC lines from the agent socket and push them to the SSE queue."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    await queue.put(line.decode())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('ACP HTTP: forward error: %s', exc)
        finally:
            await queue.put(None)

    # ── RPC (client→server) ───────────────────────────────────────────────────

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """Receive a JSON-RPC message from the client and feed it to the agent."""
        self._require_auth(request)

        conn_id = request.match_info['conn_id']
        writer = self._connections.get(conn_id)
        if writer is None:
            raise web.HTTPNotFound(text=f'Unknown connection: {conn_id}')

        body = await request.read()
        body = body.strip()
        if not body:
            return web.Response(status=204)

        writer.write(body + b'\n')
        await writer.drain()
        return web.Response(status=204)

    # ── Device flow ───────────────────────────────────────────────────────────

    async def _handle_device_request(self, request: web.Request) -> web.Response:
        """Step 1: client requests a device code to start the auth flow."""
        host = request.url.host or 'localhost'
        port = request.url.port or 80
        verification_uri = f'http://{host}:{port}/acp/auth/approve'

        code = self._device_flow.create_code(verification_uri)
        return web.json_response({
            'device_code': code.device_code,
            'user_code': code.user_code,
            'verification_uri': code.verification_uri,
            'expires_in': code.expires_in,
            'interval': code.interval,
        })

    async def _handle_token_poll(self, request: web.Request) -> web.Response:
        """Step 3: client polls for the access token after the user approves."""
        body = await request.json()
        device_code = body.get('device_code', '')
        token = self._device_flow.poll(device_code)
        if token is None:
            return web.json_response({'error': 'authorization_pending'}, status=400)
        return web.json_response({'access_token': token, 'token_type': 'Bearer'})

    async def _handle_approve(self, request: web.Request) -> web.Response:
        """Step 2: admin approves a pending device code (called by the operator owner)."""
        body = await request.json()
        device_code = body.get('device_code', '')
        token = self._device_flow.approve(device_code)
        if token is None:
            return web.json_response({'error': 'invalid_or_expired_code'}, status=400)
        return web.json_response({'status': 'approved', 'access_token': token})


async def serve_http(runtime: Runtime, host: str = '0.0.0.0', port: int = 8080) -> None:
    """Convenience wrapper — create ACPHttpServer and serve."""
    from program.acp.server import OperatorACPAgent
    from program.acp.device_flow import DeviceFlowManager
    from program.settings.paths import get_agent_dir

    agent = OperatorACPAgent(runtime)
    tokens_path = get_agent_dir() / 'auth' / 'acp_tokens.json'
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    device_flow = DeviceFlowManager(tokens_path)
    http_server = ACPHttpServer(agent, device_flow)

    app = web.Application()
    app.add_routes(http_server.routes())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info('ACP HTTP server listening on http://%s:%d', host, port)

    try:
        await asyncio.get_event_loop().create_future()  # run forever
    finally:
        await runner.cleanup()
        logger.debug('ACP HTTP server stopped')
