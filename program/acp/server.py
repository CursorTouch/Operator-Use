from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Callable

from program.acp.device_flow import DeviceFlowManager
from program.acp.types import (
    AgentListResponse, AgentMetadata,
    Run, RunCreateRequest, RunMode, RunOutputEvent, RunStatus,
    TextMessagePart,
)
from program.acp.utils import text_from_parts, parts_from_text
from program.acp.provenance import ACPProvenance

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

# Signature: (input_text, session_id) → yields text chunks
AgentRunnerFn = Callable[[str, str | None], AsyncIterator[str]]


class ACPServer:
    """
    ACP-compliant REST + WebSocket server.

    Exposes registered agents at:
        GET  /agents                    list agents
        GET  /agents/{id}               agent metadata
        GET  /agents/{id}/pubkey        Ed25519 public key
        POST /runs                      create and optionally execute a run
        GET  /runs/{id}                 poll run status
        DELETE /runs/{id}               cancel a run
        GET  /runs/{id}/await           SSE stream of RunOutputEvents
        GET  /runs/{id}/ws              WebSocket stream of RunOutputEvents

        POST /auth/device               request device code (RFC 8628)
        POST /auth/token                poll for access token
        GET  /auth/approve              approval UI (list pending codes)
        POST /auth/approve/{code}       approve a device code

    Authentication:
        - Global bearer token: unlocks all agents
        - Per-agent tokens: token → agent_id mapping
        - Device flow tokens: validated via DeviceFlowManager
        - Paths exempt from auth: /auth/*, /agents/{id}/pubkey

    Signing:
        - If provenance is set, incoming X-ACP-* headers are verified
        - Public key served at /agents/{id}/pubkey for remote verification
    """

    def __init__(
        self,
        agents: dict[str, AgentMetadata],
        runners: dict[str, AgentRunnerFn],
        *,
        host: str = '127.0.0.1',
        port: int = 8766,
        auth_token: str | None = None,
        per_agent_tokens: dict[str, str] | None = None,
        provenance: ACPProvenance | None = None,
        trusted_agents: dict[str, str] | None = None,
        device_flow: DeviceFlowManager | None = None,
        public_url: str | None = None,
    ) -> None:
        if not _AIOHTTP_AVAILABLE:
            raise ImportError('aiohttp>=3.9 is required for ACPServer.')
        self._agents   = agents
        self._runners  = runners
        self._host     = host
        self._port     = port
        self._auth_token       = auth_token
        self._per_agent_tokens = per_agent_tokens or {}
        self._provenance       = provenance
        self._trusted_agents   = trusted_agents or {}
        self._device_flow      = device_flow
        self._public_url       = public_url or f'http://{host}:{port}'

        self._runs:       dict[str, Run]                   = {}
        self._run_queues: dict[str, asyncio.Queue]         = {}
        self._app:        web.Application | None           = None
        self._runner_obj: web.AppRunner | None             = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        app = web.Application(middlewares=[self._auth_middleware, self._signature_middleware])
        app.router.add_get('/agents',                        self._list_agents)
        app.router.add_get('/agents/{agent_id}',             self._get_agent)
        app.router.add_get('/agents/{agent_id}/pubkey',      self._get_pubkey)
        app.router.add_post('/runs',                         self._create_run)
        app.router.add_get('/runs/{run_id}',                 self._get_run)
        app.router.add_delete('/runs/{run_id}',              self._cancel_run)
        app.router.add_get('/runs/{run_id}/await',           self._await_run_sse)
        app.router.add_get('/runs/{run_id}/ws',              self._await_run_ws)

        if self._device_flow:
            app.router.add_post('/auth/device',              self._auth_device)
            app.router.add_post('/auth/token',               self._auth_token_poll)
            app.router.add_get('/auth/approve',              self._auth_approve_list)
            app.router.add_post('/auth/approve/{code}',      self._auth_approve_code)

        self._app = app
        runner = web.AppRunner(app)
        self._runner_obj = runner
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        logger.info('ACP server listening on %s:%d', self._host, self._port)

    async def stop(self) -> None:
        if self._runner_obj:
            await self._runner_obj.cleanup()
            self._runner_obj = None

    # ── Middleware ────────────────────────────────────────────────────────────

    _AUTH_EXEMPT = {'/auth/device', '/auth/token', '/auth/approve'}

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        path = request.path
        # Exempt device flow + pubkey endpoints
        if path in self._AUTH_EXEMPT or path.startswith('/auth/approve/'):
            return await handler(request)
        if path.endswith('/pubkey'):
            return await handler(request)

        # No auth configured → open access
        if not self._auth_token and not self._per_agent_tokens and not self._device_flow:
            request['_authed_agent'] = None
            return await handler(request)

        auth_header = request.headers.get('Authorization', '')
        token = auth_header.removeprefix('Bearer ').strip()

        # Per-agent token
        if token in self._per_agent_tokens:
            request['_authed_agent'] = self._per_agent_tokens[token]
            return await handler(request)

        # Global token
        if self._auth_token and token == self._auth_token:
            request['_authed_agent'] = None
            return await handler(request)

        # Device flow token
        if self._device_flow and self._device_flow.validate_token(token):
            request['_authed_agent'] = None
            return await handler(request)

        raise web.HTTPUnauthorized(reason='Invalid or missing bearer token.')

    @web.middleware
    async def _signature_middleware(self, request: web.Request, handler):
        if not self._provenance:
            return await handler(request)
        if request.path.endswith('/pubkey'):
            return await handler(request)

        agent_id  = request.headers.get('X-ACP-Agent-ID')
        timestamp = request.headers.get('X-ACP-Timestamp')
        signature = request.headers.get('X-ACP-Signature')
        agent_url = request.headers.get('X-ACP-Agent-URL')

        if not all([agent_id, timestamp, signature]):
            return await handler(request)  # unsigned requests still allowed

        body = await request.read()
        pub_key = self._trusted_agents.get(agent_id)

        if not pub_key and agent_url:
            pub_key = await self._fetch_pubkey(agent_id, agent_url)

        if pub_key and not ACPProvenance.verify(agent_id, int(timestamp), body, signature, pub_key):
            raise web.HTTPForbidden(reason='Invalid ACP signature.')

        return await handler(request)

    async def _fetch_pubkey(self, agent_id: str, agent_url: str) -> str | None:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(f'{agent_url.rstrip("/")}/agents/{agent_id}/pubkey') as r:
                    if r.status == 200:
                        data = await r.json()
                        return data.get('public_key')
        except Exception:
            pass
        return None

    # ── Agent endpoints ───────────────────────────────────────────────────────

    async def _list_agents(self, request: web.Request) -> web.Response:
        scoped = request.get('_authed_agent')
        agents = (
            [self._agents[scoped]] if scoped and scoped in self._agents
            else list(self._agents.values())
        )
        return web.json_response(AgentListResponse(agents=agents).model_dump())

    async def _get_agent(self, request: web.Request) -> web.Response:
        agent_id = request.match_info['agent_id']
        meta = self._agents.get(agent_id)
        if not meta:
            raise web.HTTPNotFound(reason=f'Agent {agent_id!r} not found.')
        return web.json_response(meta.model_dump())

    async def _get_pubkey(self, request: web.Request) -> web.Response:
        agent_id = request.match_info['agent_id']
        if agent_id not in self._agents:
            raise web.HTTPNotFound(reason=f'Agent {agent_id!r} not found.')
        if not self._provenance:
            raise web.HTTPNotFound(reason='Provenance not enabled on this server.')
        return web.json_response({
            'agent_id':   agent_id,
            'algorithm':  'ed25519',
            'public_key': self._provenance.public_key_b64,
        })

    # ── Run endpoints ─────────────────────────────────────────────────────────

    async def _create_run(self, request: web.Request) -> web.Response:
        body = await request.json()
        req = RunCreateRequest.model_validate(body)

        scoped = request.get('_authed_agent')
        if scoped and req.agent_id != scoped:
            raise web.HTTPForbidden(reason='Token is scoped to a different agent.')
        if req.agent_id not in self._agents:
            raise web.HTTPNotFound(reason=f'Agent {req.agent_id!r} not found.')

        run = Run(
            agent_id=req.agent_id,
            session_id=req.session_id,
            mode=req.mode,
            input=req.input,
            metadata=req.metadata,
        )
        self._runs[run.id] = run
        self._run_queues[run.id] = asyncio.Queue()

        if req.mode == RunMode.SYNC:
            await self._execute_run(run)
            return web.json_response(run.model_dump(mode='json'), status=200)

        asyncio.create_task(self._execute_run(run))
        return web.json_response(run.model_dump(mode='json'), status=202)

    async def _get_run(self, request: web.Request) -> web.Response:
        run = self._runs.get(request.match_info['run_id'])
        if not run:
            raise web.HTTPNotFound(reason='Run not found.')
        return web.json_response(run.model_dump(mode='json'))

    async def _cancel_run(self, request: web.Request) -> web.Response:
        run = self._runs.get(request.match_info['run_id'])
        if not run:
            raise web.HTTPNotFound(reason='Run not found.')
        run.status = RunStatus.CANCELLED
        q = self._run_queues.get(run.id)
        if q:
            await q.put(None)
        return web.Response(status=204)

    # ── SSE stream ────────────────────────────────────────────────────────────

    async def _await_run_sse(self, request: web.Request) -> web.StreamResponse:
        run_id = request.match_info['run_id']
        if run_id not in self._runs:
            raise web.HTTPNotFound(reason='Run not found.')

        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'})
        await response.prepare(request)

        queue = self._run_queues[run_id]
        while True:
            chunk = await queue.get()
            if chunk is None:
                run = self._runs[run_id]
                event_type = 'error' if run.status == RunStatus.FAILED else 'completed'
                evt = RunOutputEvent(type=event_type, run_id=run_id, error=run.error)
                await response.write(f'data: {evt.model_dump_json()}\n\n'.encode())
                break
            evt = RunOutputEvent(type='output', run_id=run_id, part=TextMessagePart(text=chunk))
            await response.write(f'data: {evt.model_dump_json()}\n\n'.encode())

        await response.write_eof()
        return response

    # ── WebSocket stream ──────────────────────────────────────────────────────

    async def _await_run_ws(self, request: web.Request) -> web.WebSocketResponse:
        run_id = request.match_info['run_id']
        if run_id not in self._runs:
            raise web.HTTPNotFound(reason='Run not found.')

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        queue = self._run_queues[run_id]
        while True:
            chunk = await queue.get()
            if chunk is None:
                run = self._runs[run_id]
                event_type = 'error' if run.status == RunStatus.FAILED else 'completed'
                evt = RunOutputEvent(type=event_type, run_id=run_id, error=run.error)
                await ws.send_str(evt.model_dump_json())
                break
            evt = RunOutputEvent(type='output', run_id=run_id, part=TextMessagePart(text=chunk))
            await ws.send_str(evt.model_dump_json())

        await ws.close()
        return ws

    # ── Run execution ─────────────────────────────────────────────────────────

    async def _execute_run(self, run: Run) -> None:
        run.status = RunStatus.IN_PROGRESS
        queue = self._run_queues[run.id]
        runner = self._runners.get(run.agent_id)

        if not runner:
            run.status = RunStatus.FAILED
            run.error  = f'No runner registered for agent {run.agent_id!r}.'
            run.finished_at = datetime.now(timezone.utc)
            await queue.put(None)
            return

        input_text = text_from_parts(run.input)
        try:
            async for chunk in runner(input_text, run.session_id):
                run.output.append(TextMessagePart(text=chunk))
                await queue.put(chunk)
            run.status = RunStatus.COMPLETED
        except Exception as exc:
            logger.exception('ACP run %s failed', run.id)
            run.status = RunStatus.FAILED
            run.error  = str(exc)

        run.finished_at = datetime.now(timezone.utc)
        await queue.put(None)

    # ── Device flow endpoints ─────────────────────────────────────────────────

    async def _auth_device(self, request: web.Request) -> web.Response:
        assert self._device_flow
        verification_uri = f'{self._public_url}/auth/approve'
        resp = self._device_flow.create_code(verification_uri)
        return web.json_response(resp.model_dump())

    async def _auth_token_poll(self, request: web.Request) -> web.Response:
        assert self._device_flow
        body = await request.json()
        device_code = body.get('device_code', '')
        token = self._device_flow.poll(device_code)
        if token:
            return web.json_response({'access_token': token})
        return web.json_response({'detail': 'authorization_pending'}, status=202)

    async def _auth_approve_list(self, request: web.Request) -> web.Response:
        assert self._device_flow
        pending = self._device_flow.list_pending()
        rows = ''.join(
            f'<tr><td>{p.user_code}</td>'
            f'<td><form method="post" action="/auth/approve/{p.device_code}">'
            f'<button type="submit">Approve</button></form></td></tr>'
            for p in pending
        )
        html = (
            f'<html><body><h2>Pending ACP authorizations</h2>'
            f'<table border="1"><tr><th>Code</th><th>Action</th></tr>{rows}</table>'
            f'</body></html>'
        )
        return web.Response(text=html, content_type='text/html')

    async def _auth_approve_code(self, request: web.Request) -> web.Response:
        assert self._device_flow
        code = request.match_info['code']
        token = self._device_flow.approve(code)
        if not token:
            raise web.HTTPNotFound(reason='Code not found or expired.')
        return web.Response(text='<html><body><p>Approved. You may close this tab.</p></body></html>',
                            content_type='text/html')
