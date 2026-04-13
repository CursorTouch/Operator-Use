"""ACP client — call a remote ACP-compatible agent (e.g. Claude Code).

Usage:
    async with ACPClient(config) as client:
        # One-shot: wait for full response
        output = await client.run("refactor the auth module", session_id="s1")

        # Streaming: get chunks as they arrive
        async for chunk in client.run_stream("explain this code"):
            print(chunk, end="", flush=True)
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import aiohttp

from operator_use.acp.config import ACPClientConfig
from operator_use.acp.provenance import ACPProvenance
from operator_use.acp.models import (
    AgentListResponse,
    AgentMetadata,
    MessagePart,
    Run,
    RunCreateRequest,
    RunMode,
    RunStatus,
    TextMessagePart,
)

logger = logging.getLogger(__name__)


class ACPClient:
    """Async HTTP client for ACP-compliant agent servers."""

    def __init__(self, config: ACPClientConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._provenance: ACPProvenance | None = None
        if config.sign_requests:
            self._provenance = (
                ACPProvenance.load_or_generate(config.key_path)
                if config.key_path
                else ACPProvenance.generate()
            )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ACPClient":
        self._session = aiohttp.ClientSession(
            base_url=self.config.base_url,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    async def run(
        self,
        text: str,
        session_id: str | None = None,
        extra_parts: list[MessagePart] | None = None,
    ) -> str:
        """Send `text` to the remote agent and return the full text response."""
        parts: list[MessagePart] = [TextMessagePart(text=text)] + (extra_parts or [])
        req = RunCreateRequest(
            agent_id=self.config.agent_id,
            session_id=session_id,
            mode=RunMode.SYNC,
            input=parts,
        )
        run = await self._create_run(req)
        if run.status == RunStatus.COMPLETED:
            return self._output_to_text(run)
        raise RuntimeError(f"ACP run {run.id} finished with status {run.status}: {run.error}")

    async def run_stream(
        self,
        text: str,
        session_id: str | None = None,
        extra_parts: list[MessagePart] | None = None,
    ) -> AsyncIterator[str]:
        """Send `text` and yield response chunks via SSE as they arrive."""
        parts: list[MessagePart] = [TextMessagePart(text=text)] + (extra_parts or [])
        req = RunCreateRequest(
            agent_id=self.config.agent_id,
            session_id=session_id,
            mode=RunMode.STREAM,
            input=parts,
        )
        run = await self._create_run(req)
        async for chunk in self._await_run_sse(run.id):
            yield chunk

    # ------------------------------------------------------------------
    # Agent discovery
    # ------------------------------------------------------------------

    async def list_agents(self) -> AgentListResponse:
        async with self._get("/agents") as resp:
            resp.raise_for_status()
            return AgentListResponse(**await resp.json())

    async def get_agent(self, agent_id: str | None = None) -> AgentMetadata:
        aid = agent_id or self.config.agent_id
        async with self._get(f"/agents/{aid}") as resp:
            resp.raise_for_status()
            return AgentMetadata(**await resp.json())

    # ------------------------------------------------------------------
    # Run management
    # ------------------------------------------------------------------

    async def get_run(self, run_id: str) -> Run:
        async with self._get(f"/runs/{run_id}") as resp:
            resp.raise_for_status()
            return Run(**await resp.json())

    async def cancel_run(self, run_id: str) -> None:
        async with self._delete(f"/runs/{run_id}") as resp:
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _create_run(self, req: RunCreateRequest) -> Run:
        session = self._ensure_session()
        body = json.dumps(req.model_dump(mode="json")).encode()
        headers = self._signature_headers(body)
        async with session.post(
            "/runs", data=body, headers={**headers, "Content-Type": "application/json"}
        ) as resp:
            resp.raise_for_status()
            return Run(**await resp.json())

    async def _await_run_sse(self, run_id: str) -> AsyncIterator[str]:
        """Stream SSE events from /runs/{run_id}/await and yield text chunks."""
        session = self._ensure_session()
        async with session.get(f"/runs/{run_id}/await") as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "output":
                    part = event.get("part") or {}
                    if part.get("type") == "text":
                        yield part.get("text", "")
                elif event.get("type") in ("completed", "error"):
                    break

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("ACPClient must be used as an async context manager")
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        if self.config.auth_token:
            return {"Authorization": f"Bearer {self.config.auth_token}"}
        return {}

    def _signature_headers(self, body: bytes) -> dict[str, str]:
        """Build X-ACP-* signature headers if sign_requests is enabled."""
        if not self._provenance:
            return {}
        return self._provenance.auth_headers(
            agent_id=self.config.agent_id_self,
            body=body,
            agent_url=self.config.public_url or None,
        )

    def _get(self, path: str):
        return self._ensure_session().get(path)

    def _delete(self, path: str):
        return self._ensure_session().delete(path)

    @staticmethod
    def _output_to_text(run: Run) -> str:
        parts = []
        for p in run.output:
            if isinstance(p, TextMessagePart):
                parts.append(p.text)
        return "".join(parts)
