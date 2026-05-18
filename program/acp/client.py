from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from program.acp.types import (
    AgentListResponse, AgentMetadata,
    Run, RunCreateRequest, RunMode, RunOutputEvent,
    MessagePart, TextMessagePart,
    DeviceCodeResponse, TokenResponse,
)
from program.acp.utils import text_from_parts, parts_from_text
from program.acp.provenance import ACPProvenance

logger = logging.getLogger(__name__)

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

try:
    from websockets.asyncio.client import connect as ws_connect
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class ACPClient:
    """
    Async client for calling remote ACP agents.

    Supports three transports (auto-negotiated per call):
        - WebSocket  — connect to /runs/{id}/ws for streaming
        - SSE        — GET /runs/{id}/await for streaming (fallback)
        - HTTP SYNC  — POST /runs with mode=sync for blocking calls

    Usage:
        async with ACPClient(base_url='http://remote:8766', agent_id='operator') as client:
            text = await client.run('summarise this file')
            async for chunk in client.run_stream('refactor auth.py'):
                print(chunk, end='', flush=True)
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        *,
        auth_token: str | None = None,
        provenance: ACPProvenance | None = None,
        agent_url: str | None = None,
        timeout: float = 120.0,
        prefer_websocket: bool = True,
    ) -> None:
        if not _AIOHTTP_AVAILABLE:
            raise ImportError('aiohttp>=3.9 is required for ACPClient.')
        self._base_url        = base_url.rstrip('/')
        self._agent_id        = agent_id
        self._auth_token      = auth_token
        self._provenance      = provenance
        self._agent_url       = agent_url
        self._timeout         = aiohttp.ClientTimeout(total=timeout)
        self._prefer_ws       = prefer_websocket
        self._session: aiohttp.ClientSession | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> ACPClient:
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── High-level API ────────────────────────────────────────────────────────

    async def run(
        self,
        text: str,
        session_id: str | None = None,
        extra_parts: list[MessagePart] | None = None,
    ) -> str:
        """Send a prompt and return the full response text (blocking)."""
        req = RunCreateRequest(
            agent_id=self._agent_id,
            session_id=session_id,
            mode=RunMode.SYNC,
            input=parts_from_text(text) + (extra_parts or []),
        )
        run = await self._create_run(req)
        return text_from_parts(run.output)

    async def run_stream(
        self,
        text: str,
        session_id: str | None = None,
        extra_parts: list[MessagePart] | None = None,
    ) -> AsyncIterator[str]:
        """Send a prompt and yield response text chunks as they arrive."""
        req = RunCreateRequest(
            agent_id=self._agent_id,
            session_id=session_id,
            mode=RunMode.STREAM,
            input=parts_from_text(text) + (extra_parts or []),
        )
        run = await self._create_run(req)

        if self._prefer_ws and _WS_AVAILABLE:
            async for chunk in self._stream_ws(run.id):
                yield chunk
        else:
            async for chunk in self._stream_sse(run.id):
                yield chunk

    # ── Agent discovery ───────────────────────────────────────────────────────

    async def list_agents(self) -> AgentListResponse:
        async with self._get('/agents') as r:
            return AgentListResponse.model_validate(await r.json())

    async def get_agent(self, agent_id: str | None = None) -> AgentMetadata:
        aid = agent_id or self._agent_id
        async with self._get(f'/agents/{aid}') as r:
            return AgentMetadata.model_validate(await r.json())

    # ── Run management ────────────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> Run:
        async with self._get(f'/runs/{run_id}') as r:
            return Run.model_validate(await r.json())

    async def cancel_run(self, run_id: str) -> None:
        assert self._session
        async with self._session.delete(
            f'{self._base_url}/runs/{run_id}',
            headers=self._auth_headers(),
        ) as r:
            r.raise_for_status()

    # ── Device auth (RFC 8628) ────────────────────────────────────────────────

    async def device_auth(self, poll_interval: float | None = None) -> str:
        """Interactive device authorization flow. Returns the access token."""
        async with self._post('/auth/device', {}) as r:
            resp = DeviceCodeResponse.model_validate(await r.json())

        interval = poll_interval or resp.interval
        print(f'\nOpen this URL and enter the code: {resp.verification_uri}')
        print(f'Code: {resp.user_code}\n')

        import asyncio
        while True:
            await asyncio.sleep(interval)
            async with self._post('/auth/token', {'device_code': resp.device_code}) as r:
                if r.status == 200:
                    token_resp = TokenResponse.model_validate(await r.json())
                    self._auth_token = token_resp.access_token
                    self._session = aiohttp.ClientSession(timeout=self._timeout)
                    return token_resp.access_token
                # 202 = pending, keep polling

    # ── Internal: create run ──────────────────────────────────────────────────

    async def _create_run(self, req: RunCreateRequest) -> Run:
        body = req.model_dump_json().encode()
        headers = {**self._auth_headers(), 'Content-Type': 'application/json'}
        if self._provenance:
            headers.update(self._provenance.auth_headers(self._agent_id, body, self._agent_url))
        assert self._session
        async with self._session.post(
            f'{self._base_url}/runs',
            data=body,
            headers=headers,
        ) as r:
            r.raise_for_status()
            return Run.model_validate(await r.json())

    # ── Internal: SSE stream ──────────────────────────────────────────────────

    async def _stream_sse(self, run_id: str) -> AsyncIterator[str]:
        assert self._session
        async with self._session.get(
            f'{self._base_url}/runs/{run_id}/await',
            headers=self._auth_headers(),
        ) as r:
            r.raise_for_status()
            async for raw in r.content:
                line = raw.strip()
                if not line.startswith(b'data:'):
                    continue
                payload = line[5:].strip()
                try:
                    evt = RunOutputEvent.model_validate_json(payload)
                except Exception:
                    continue
                if evt.type == 'output' and isinstance(evt.part, TextMessagePart):
                    yield evt.part.text
                elif evt.type in ('completed', 'error'):
                    break

    # ── Internal: WebSocket stream ────────────────────────────────────────────

    async def _stream_ws(self, run_id: str) -> AsyncIterator[str]:
        ws_url = self._base_url.replace('http://', 'ws://').replace('https://', 'wss://')
        url = f'{ws_url}/runs/{run_id}/ws'
        extra_headers = self._auth_headers()
        try:
            async with ws_connect(url, additional_headers=extra_headers) as ws:
                async for raw in ws:
                    try:
                        evt = RunOutputEvent.model_validate_json(raw)
                    except Exception:
                        continue
                    if evt.type == 'output' and isinstance(evt.part, TextMessagePart):
                        yield evt.part.text
                    elif evt.type in ('completed', 'error'):
                        break
        except Exception:
            logger.debug('ACPClient: WebSocket failed, falling back to SSE for run %s', run_id)
            async for chunk in self._stream_sse(run_id):
                yield chunk

    # ── Request helpers ───────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_token:
            return {'Authorization': f'Bearer {self._auth_token}'}
        return {}

    def _get(self, path: str):
        assert self._session
        return self._session.get(f'{self._base_url}{path}', headers=self._auth_headers())

    def _post(self, path: str, body: dict):
        assert self._session
        return self._session.post(
            f'{self._base_url}{path}',
            json=body,
            headers=self._auth_headers(),
        )
