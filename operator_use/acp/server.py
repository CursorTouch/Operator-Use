"""ACP server — exposes Operator as an ACP-compliant REST agent.

Endpoints:
  GET  /agents                 -> AgentListResponse
  GET  /agents/{agent_id}      -> AgentMetadata
  POST /runs                   -> Run (creates and optionally awaits)
  GET  /runs/{run_id}          -> Run
  DELETE /runs/{run_id}        -> 204 (cancel)
  GET  /runs/{run_id}/await    -> SSE stream of RunOutputEvent

Usage:
    server = ACPServer(
        config=config,
        runners={"agent-a": runner_a, "agent-b": runner_b},
        metadata={"agent-a": AgentMetadata(...), "agent-b": AgentMetadata(...)},
    )
    await server.start()
    ...
    await server.stop()

Each runner is a callable: async (input_text: str, session_id: str | None) -> AsyncIterator[str]
`runners` and `metadata` share the same keys (agent IDs).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Callable

from aiohttp import web

from operator_use.acp.config import ACPServerConfig
from operator_use.acp.provenance import ACPProvenance, fetch_public_key
from operator_use.acp.models import (
    AgentListResponse,
    AgentMetadata,
    MessagePart,
    Run,
    RunCreateRequest,
    RunMode,
    RunOutputEvent,
    RunStatus,
    TextMessagePart,
)

logger = logging.getLogger(__name__)

# Type alias for the agent runner callback
AgentRunnerFn = Callable[[str, str | None], AsyncIterator[str]]


def _input_to_text(parts: list[MessagePart]) -> str:
    """Flatten ACP input parts to a plain-text string for the agent."""
    lines: list[str] = []
    for p in parts:
        if isinstance(p, TextMessagePart):
            lines.append(p.text)
    return "\n".join(lines)


class ACPServer:
    """Async ACP REST server — routes runs to one of N registered agent runners."""

    def __init__(
        self,
        config: ACPServerConfig,
        runners: dict[str, AgentRunnerFn],
        metadata: dict[str, AgentMetadata],
    ) -> None:
        self.config = config
        self._runners = runners  # agent_id -> runner callable
        self._metadata = metadata  # agent_id -> AgentMetadata
        self._runs: dict[str, Run] = {}
        self._run_queues: dict[str, asyncio.Queue] = {}  # run_id -> chunk queue for SSE
        # Provenance: loaded lazily when sign_responses or verify_signatures is True
        self._provenance: ACPProvenance | None = None
        # Cache of fetched peer public keys: agent_id -> public_key_b64
        self._peer_pubkeys: dict[str, str] = dict(config.trusted_agents)
        self._app = self._build_app()
        self._site: web.TCPSite | None = None
        self._runner_obj: web.AppRunner | None = None

    # ------------------------------------------------------------------
    # App / routing
    # ------------------------------------------------------------------

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware, self._signature_middleware])
        app.router.add_get("/agents", self._handle_list_agents)
        app.router.add_get("/agents/{agent_id}", self._handle_get_agent)
        app.router.add_get("/agents/{agent_id}/pubkey", self._handle_get_pubkey)
        app.router.add_post("/runs", self._handle_create_run)
        app.router.add_get("/runs/{run_id}", self._handle_get_run)
        app.router.add_delete("/runs/{run_id}", self._handle_cancel_run)
        app.router.add_get("/runs/{run_id}/await", self._handle_await_run)
        return app

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Authenticate the request and stash the allowed agent scope.

        Per-agent tokens (per_agent_tokens) take precedence over the global auth_token.
        When per-agent tokens are configured, each token grants access to exactly one agent —
        the caller cannot see or reach any other agent on this server.
        request["_authed_agent"]:
          - str  → caller is locked to this agent_id
          - None → global access (all agents allowed)
        """
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        if self.config.per_agent_tokens:
            # Reverse-lookup: which agent owns this token?
            agent_id = next(
                (aid for aid, t in self.config.per_agent_tokens.items() if t == provided),
                None,
            )
            if agent_id is None:
                return web.Response(status=401, text="Unauthorized")
            request["_authed_agent"] = agent_id
        elif self.config.auth_token:
            if provided != self.config.auth_token:
                return web.Response(status=401, text="Unauthorized")
            request["_authed_agent"] = None  # global — all agents accessible
        else:
            request["_authed_agent"] = None  # no auth configured — open access

        return await handler(request)

    @web.middleware
    async def _signature_middleware(self, request: web.Request, handler):
        """Verify Ed25519 signatures on incoming requests when enabled."""
        if not self.config.verify_signatures:
            return await handler(request)

        # Pubkey endpoint is always open (needed for key discovery)
        if request.path.endswith("/pubkey"):
            return await handler(request)

        agent_id = request.headers.get("X-ACP-Agent-ID")
        timestamp_str = request.headers.get("X-ACP-Timestamp")
        signature = request.headers.get("X-ACP-Signature")

        if not (agent_id and timestamp_str and signature):
            return web.Response(status=401, text="Missing X-ACP-* signature headers")

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            return web.Response(status=400, text="Invalid X-ACP-Timestamp")

        # Resolve public key: config cache → auto-discovery via X-ACP-Agent-URL
        pubkey = self._peer_pubkeys.get(agent_id)
        if not pubkey:
            agent_url = request.headers.get("X-ACP-Agent-URL")
            if agent_url:
                pubkey = await fetch_public_key(agent_url, agent_id)
                if pubkey:
                    self._peer_pubkeys[agent_id] = pubkey  # cache it
        if not pubkey:
            return web.Response(status=401, text=f"Unknown agent: {agent_id!r}")

        body = await request.read()
        if not ACPProvenance.verify(agent_id, timestamp, body, signature, pubkey):
            return web.Response(status=401, text="Invalid signature")

        return await handler(request)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_list_agents(self, request: web.Request) -> web.Response:
        authed: str | None = request.get("_authed_agent")
        if authed is not None:
            agents = [self._metadata[authed]] if authed in self._metadata else []
        else:
            agents = list(self._metadata.values())
        resp = AgentListResponse(id=self.config.id, agents=agents)
        return web.json_response(resp.model_dump())

    async def _handle_get_agent(self, request: web.Request) -> web.Response:
        agent_id = request.match_info["agent_id"]
        authed: str | None = request.get("_authed_agent")
        if authed is not None and authed != agent_id:
            return web.json_response({"error": "agent not found"}, status=404)
        meta = self._metadata.get(agent_id)
        if not meta:
            return web.json_response({"error": "agent not found"}, status=404)
        return web.json_response(meta.model_dump())

    async def _handle_get_pubkey(self, request: web.Request) -> web.Response:
        """Return this agent's Ed25519 public key for signature verification."""
        agent_id = request.match_info["agent_id"]
        authed: str | None = request.get("_authed_agent")
        if authed is not None and authed != agent_id:
            return web.json_response({"error": "agent not found"}, status=404)
        if agent_id not in self._metadata:
            return web.json_response({"error": "agent not found"}, status=404)
        if not self._provenance:
            return web.json_response({"error": "provenance not enabled"}, status=404)
        return web.json_response(
            {
                "agent_id": agent_id,
                "algorithm": "ed25519",
                "public_key": self._provenance.public_key_b64,
            }
        )

    async def _handle_create_run(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            req = RunCreateRequest(**body)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

        if not self._runners:
            return web.json_response({"error": "no agents configured"}, status=503)

        authed: str | None = request.get("_authed_agent")

        # Resolve target agent: use requested agent_id, fall back to first available
        target_agent_id = (
            req.agent_id if req.agent_id in self._runners else next(iter(self._runners))
        )

        # Per-agent token: caller may only target their own agent
        if authed is not None and target_agent_id != authed:
            return web.json_response(
                {"error": f"not authorized to run agent '{target_agent_id}'"},
                status=403,
            )

        run = Run(
            agent_id=target_agent_id,
            session_id=req.session_id,
            mode=req.mode,
            input=req.input,
            metadata=req.metadata,
            status=RunStatus.CREATED,
        )
        self._runs[run.id] = run
        self._run_queues[run.id] = asyncio.Queue()

        if req.mode == RunMode.SYNC:
            await self._execute_run(run)
            return web.json_response(run.model_dump(mode="json"))

        # ASYNC / STREAM: fire and forget
        asyncio.create_task(self._execute_run(run))
        return web.json_response(run.model_dump(mode="json"), status=202)

    async def _handle_get_run(self, request: web.Request) -> web.Response:
        run = self._runs.get(request.match_info["run_id"])
        if not run:
            return web.json_response({"error": "run not found"}, status=404)
        return web.json_response(run.model_dump(mode="json"))

    async def _handle_cancel_run(self, request: web.Request) -> web.Response:
        run = self._runs.get(request.match_info["run_id"])
        if not run:
            return web.json_response({"error": "run not found"}, status=404)
        if run.status in (RunStatus.CREATED, RunStatus.IN_PROGRESS, RunStatus.AWAITING):
            run.status = RunStatus.CANCELLED
            q = self._run_queues.get(run.id)
            if q:
                await q.put(None)  # sentinel
        return web.Response(status=204)

    async def _handle_await_run(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint — streams RunOutputEvent JSON lines until run finishes."""
        run = self._runs.get(request.match_info["run_id"])
        if not run:
            return web.json_response({"error": "run not found"}, status=404)

        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await response.prepare(request)

        queue = self._run_queues.get(run.id)
        if not queue:
            await response.write_eof()
            return response

        async def _send_event(event: RunOutputEvent) -> None:
            data = f"data: {event.model_dump_json()}\n\n"
            await response.write(data.encode())

        while True:
            item = await queue.get()
            if item is None:  # sentinel — run finished or cancelled
                break
            if isinstance(item, str):
                evt = RunOutputEvent(
                    type="output",
                    run_id=run.id,
                    part=TextMessagePart(text=item),
                )
                await _send_event(evt)

        # Send completion event
        final_type = "completed" if run.status == RunStatus.COMPLETED else "error"
        await _send_event(
            RunOutputEvent(
                type=final_type,
                run_id=run.id,
                error=run.error if run.status == RunStatus.FAILED else None,
            )
        )
        await response.write_eof()
        return response

    # ------------------------------------------------------------------
    # Run execution
    # ------------------------------------------------------------------

    async def _execute_run(self, run: Run) -> None:
        from datetime import datetime

        runner = self._runners.get(run.agent_id) or next(iter(self._runners.values()), None)
        if runner is None:
            run.status = RunStatus.FAILED
            run.error = "No runner available"
            run.finished_at = datetime.utcnow()
            await self._run_queues[run.id].put(None)
            return

        run.status = RunStatus.IN_PROGRESS
        queue = self._run_queues[run.id]
        input_text = _input_to_text(run.input)
        output_parts: list[MessagePart] = []

        try:
            async for chunk in runner(input_text, run.session_id):
                if run.status == RunStatus.CANCELLED:
                    break
                output_parts.append(TextMessagePart(text=chunk))
                await queue.put(chunk)

            run.output = output_parts
            run.status = RunStatus.COMPLETED
        except Exception as e:
            logger.error(f"ACP run {run.id} failed: {e}", exc_info=True)
            run.status = RunStatus.FAILED
            run.error = str(e)
        finally:
            run.finished_at = datetime.utcnow()
            await queue.put(None)  # sentinel

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self.config.sign_responses or self.config.verify_signatures:
            self._provenance = self._load_provenance()
            logger.info(f"ACP provenance enabled — pubkey: {self._provenance.public_key_b64[:16]}…")
        self._runner_obj = web.AppRunner(self._app)
        await self._runner_obj.setup()
        self._site = web.TCPSite(self._runner_obj, self.config.host, self.config.port)
        await self._site.start()
        logger.info(f"ACP server listening on http://{self.config.host}:{self.config.port}")

    def _load_provenance(self) -> ACPProvenance:
        if self.config.key_path:
            return ACPProvenance.load_or_generate(self.config.key_path)
        return ACPProvenance.generate()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner_obj:
            await self._runner_obj.cleanup()
        logger.info("ACP server stopped")
