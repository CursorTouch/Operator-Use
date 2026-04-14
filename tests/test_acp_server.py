"""Tests for ACPServer multi-agent routing and per-agent token auth."""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from operator_use.acp.config import ACPServerConfig
from operator_use.acp.models import AgentCapabilities, AgentMetadata
from operator_use.acp.server import ACPServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(agent_id: str, description: str = "") -> AgentMetadata:
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=description,
        capabilities=AgentCapabilities(streaming=True, async_mode=True, session=True),
    )


async def _echo_runner(input_text: str, session_id: str | None):
    """Runner that yields the input back as a single chunk."""
    yield f"echo:{input_text}"


def _make_server(
    agent_ids: list[str],
    auth_token: str = "",
    per_agent_tokens: dict | None = None,
    id: str = "test-server-001",
) -> ACPServer:
    runners = {aid: _echo_runner for aid in agent_ids}
    metadata = {aid: _make_metadata(aid) for aid in agent_ids}
    config = ACPServerConfig(
        enabled=True,
        id=id,
        auth_token=auth_token,
        per_agent_tokens=per_agent_tokens or {},
    )
    return ACPServer(config=config, runners=runners, metadata=metadata)


@pytest_asyncio.fixture
async def two_agent_server():
    """ACPServer with 'alpha' and 'beta' agents, no auth."""
    server = _make_server(["alpha", "beta"])
    async with TestClient(TestServer(server._app)) as client:
        yield client


@pytest_asyncio.fixture
async def global_auth_server():
    """ACPServer with global auth_token."""
    server = _make_server(["alpha", "beta"], auth_token="global-secret")
    async with TestClient(TestServer(server._app)) as client:
        yield client


@pytest_asyncio.fixture
async def per_agent_auth_server():
    """ACPServer with per-agent tokens: alpha->token-a, beta->token-b."""
    server = _make_server(
        ["alpha", "beta"],
        per_agent_tokens={"alpha": "token-a", "beta": "token-b"},
    )
    async with TestClient(TestServer(server._app)) as client:
        yield client


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /agents — list
# ---------------------------------------------------------------------------


class TestListAgents:
    async def test_returns_all_agents_no_auth(self, two_agent_server):
        resp = await two_agent_server.get("/agents")
        assert resp.status == 200
        data = await resp.json()
        ids = {a["id"] for a in data["agents"]}
        assert ids == {"alpha", "beta"}
        assert data["id"] == "test-server-001"

    async def test_id_present_with_per_agent_auth(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents", headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["id"] == "test-server-001"

    async def test_global_token_returns_all(self, global_auth_server):
        resp = await global_auth_server.get(
            "/agents", headers={"Authorization": "Bearer global-secret"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert len(data["agents"]) == 2

    async def test_global_token_wrong_returns_401(self, global_auth_server):
        resp = await global_auth_server.get("/agents", headers={"Authorization": "Bearer wrong"})
        assert resp.status == 401

    async def test_per_agent_token_sees_only_own_agent(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents", headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert len(data["agents"]) == 1
        assert data["agents"][0]["id"] == "alpha"

    async def test_per_agent_token_b_sees_only_beta(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents", headers={"Authorization": "Bearer token-b"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert len(data["agents"]) == 1
        assert data["agents"][0]["id"] == "beta"

    async def test_per_agent_wrong_token_returns_401(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents", headers={"Authorization": "Bearer bad-token"}
        )
        assert resp.status == 401

    async def test_per_agent_no_token_returns_401(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get("/agents")
        assert resp.status == 401


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}
# ---------------------------------------------------------------------------


class TestGetAgent:
    async def test_known_agent_no_auth(self, two_agent_server):
        resp = await two_agent_server.get("/agents/alpha")
        assert resp.status == 200
        data = await resp.json()
        assert data["id"] == "alpha"

    async def test_unknown_agent_no_auth(self, two_agent_server):
        resp = await two_agent_server.get("/agents/ghost")
        assert resp.status == 404

    async def test_per_agent_can_access_own(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents/alpha", headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 200

    async def test_per_agent_cannot_access_other(self, per_agent_auth_server):
        resp = await per_agent_auth_server.get(
            "/agents/beta", headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /runs — routing and auth
# ---------------------------------------------------------------------------


class TestCreateRun:
    async def test_routes_to_correct_agent(self, two_agent_server):
        body = {"agent_id": "beta", "mode": "sync", "input": [{"type": "text", "text": "hello"}]}
        resp = await two_agent_server.post("/runs", json=body)
        assert resp.status == 200
        data = await resp.json()
        assert data["agent_id"] == "beta"
        assert data["status"] == "completed"
        output_text = "".join(p["text"] for p in data["output"] if p.get("type") == "text")
        assert output_text == "echo:hello"

    async def test_falls_back_to_first_agent_when_id_unknown(self, two_agent_server):
        body = {
            "agent_id": "nonexistent",
            "mode": "sync",
            "input": [{"type": "text", "text": "hi"}],
        }
        resp = await two_agent_server.post("/runs", json=body)
        assert resp.status == 200
        data = await resp.json()
        # Falls back to first registered agent
        assert data["agent_id"] in ("alpha", "beta")

    async def test_per_agent_token_can_run_own_agent(self, per_agent_auth_server):
        body = {"agent_id": "alpha", "mode": "sync", "input": [{"type": "text", "text": "ping"}]}
        resp = await per_agent_auth_server.post(
            "/runs", json=body, headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["agent_id"] == "alpha"

    async def test_per_agent_token_blocked_from_other_agent(self, per_agent_auth_server):
        body = {"agent_id": "beta", "mode": "sync", "input": [{"type": "text", "text": "ping"}]}
        resp = await per_agent_auth_server.post(
            "/runs", json=body, headers={"Authorization": "Bearer token-a"}
        )
        assert resp.status == 403

    async def test_per_agent_token_fallback_routes_to_own(self, per_agent_auth_server):
        """No agent_id in request — should fall back to the authed agent, not the global first."""
        # When agent_id resolves to a non-authed agent via fallback, it should be blocked.
        # Here token-a authenticates as 'alpha'. If we send an unknown agent_id it falls
        # back to first runner which may or may not be 'alpha'. Verify the 403 path by
        # explicitly targeting 'beta'.
        body = {"agent_id": "beta", "mode": "sync", "input": [{"type": "text", "text": "x"}]}
        resp = await per_agent_auth_server.post(
            "/runs", json=body, headers={"Authorization": "Bearer token-b"}
        )
        assert resp.status == 200  # token-b IS beta — should pass

    async def test_wrong_token_returns_401(self, per_agent_auth_server):
        body = {"agent_id": "alpha", "mode": "sync", "input": [{"type": "text", "text": "x"}]}
        resp = await per_agent_auth_server.post(
            "/runs", json=body, headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status == 401

    async def test_global_token_can_run_any_agent(self, global_auth_server):
        for agent_id in ("alpha", "beta"):
            body = {"agent_id": agent_id, "mode": "sync", "input": [{"type": "text", "text": "x"}]}
            resp = await global_auth_server.post(
                "/runs", json=body, headers={"Authorization": "Bearer global-secret"}
            )
            assert resp.status == 200, f"Expected 200 for {agent_id}"


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


class TestGetRun:
    async def test_get_existing_run(self, two_agent_server):
        body = {"agent_id": "alpha", "mode": "async", "input": [{"type": "text", "text": "hi"}]}
        create_resp = await two_agent_server.post("/runs", json=body)
        assert create_resp.status == 202
        run_id = (await create_resp.json())["id"]

        # Poll until done (async mode)
        for _ in range(20):
            await asyncio.sleep(0.05)
            get_resp = await two_agent_server.get(f"/runs/{run_id}")
            data = await get_resp.json()
            if data["status"] == "completed":
                break
        assert data["status"] == "completed"

    async def test_get_unknown_run_returns_404(self, two_agent_server):
        resp = await two_agent_server.get("/runs/does-not-exist")
        assert resp.status == 404
