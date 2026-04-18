"""Tests for ACP Device Authorization Grant flow."""

from __future__ import annotations

import json
import os
import time
import pytest

from operator_use.acp.device_flow import DeviceFlowManager


# ---------------------------------------------------------------------------
# DeviceFlowManager unit tests
# ---------------------------------------------------------------------------


def test_create_code_returns_device_and_user_code(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    assert code.device_code
    assert "-" in code.user_code          # format: XXXX-XXXX
    assert len(code.user_code) == 9       # 4 + dash + 4
    assert code.verification_uri == "http://localhost:8765/auth/approve"
    assert code.expires_in == 600
    assert code.interval == 5


def test_poll_returns_none_when_pending(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    assert mgr.poll(code.device_code) is None


def test_approve_then_poll_returns_token(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    token = mgr.approve(code.device_code)
    assert token is not None
    assert token.startswith("op_")
    assert mgr.poll(code.device_code) == token


def test_validate_token_true_after_approve(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    token = mgr.approve(code.device_code)
    assert mgr.validate_token(token) is True


def test_validate_token_false_for_unknown(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    assert mgr.validate_token("op_doesnotexist") is False


def test_tokens_persisted_to_disk(tmp_path):
    path = str(tmp_path / "tokens.json")
    mgr = DeviceFlowManager(tokens_path=path)
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    token = mgr.approve(code.device_code)

    # Load a fresh manager from the same file
    mgr2 = DeviceFlowManager(tokens_path=path)
    assert mgr2.validate_token(token) is True


def test_list_pending_returns_active_codes(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    pending = mgr.list_pending()
    assert len(pending) == 2


def test_list_pending_excludes_approved(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost:8765/auth/approve")
    mgr.approve(code.device_code)
    assert len(mgr.list_pending()) == 0


def test_approve_unknown_code_returns_none(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    assert mgr.approve("bad-device-code") is None


def test_poll_unknown_code_returns_none(tmp_path):
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    assert mgr.poll("bad-device-code") is None


def test_approve_expired_code_returns_none(tmp_path):
    import time
    mgr = DeviceFlowManager(tokens_path=str(tmp_path / "tokens.json"))
    code = mgr.create_code(verification_uri="http://localhost/auth/approve")
    mgr._pending[code.device_code].expires_at = time.monotonic() - 1
    assert mgr.approve(code.device_code) is None
    assert mgr.poll(code.device_code) is None


# ---------------------------------------------------------------------------
# Server endpoint tests
# ---------------------------------------------------------------------------

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from operator_use.acp.config import ACPServerConfig
from operator_use.acp.models import AgentCapabilities, AgentMetadata
from operator_use.acp.server import ACPServer


def _make_df_server(tmp_path) -> ACPServer:
    async def _echo(text, session_id):
        yield f"echo:{text}"

    config = ACPServerConfig(
        device_flow_enabled=True,
        tokens_path=str(tmp_path / "tokens.json"),
        id="test-df-server",
    )
    meta = AgentMetadata(
        id="operator",
        name="Operator",
        capabilities=AgentCapabilities(streaming=True, async_mode=True, session=True),
    )
    return ACPServer(config=config, runners={"operator": _echo}, metadata={"operator": meta})


@pytest.fixture
def df_server(tmp_path):
    return _make_df_server(tmp_path)


@pytest.mark.asyncio
async def test_post_auth_device_returns_code(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        assert resp.status == 200
        data = await resp.json()
        assert "device_code" in data
        assert "user_code" in data
        assert "-" in data["user_code"]
        assert "verification_uri" in data
        assert data["interval"] == 5


@pytest.mark.asyncio
async def test_post_auth_token_pending_returns_202(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        data = await resp.json()
        token_resp = await client.post("/auth/token", json={"device_code": data["device_code"]})
        assert token_resp.status == 202


@pytest.mark.asyncio
async def test_post_auth_token_approved_returns_token(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        data = await resp.json()
        device_code = data["device_code"]

        # Approve directly via manager
        df_server._device_flow.approve(device_code)

        token_resp = await client.post("/auth/token", json={"device_code": device_code})
        assert token_resp.status == 200
        token_data = await token_resp.json()
        assert token_data["access_token"].startswith("op_")


@pytest.mark.asyncio
async def test_get_auth_approve_shows_pending(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        await client.post("/auth/device")
        resp = await client.get("/auth/approve")
        assert resp.status == 200
        text = await resp.text()
        assert "XXXX" not in text   # rendered, not template literal
        assert "<form" in text


@pytest.mark.asyncio
async def test_post_auth_approve_approves_code(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        device_code = (await resp.json())["device_code"]

        approve_resp = await client.post(f"/auth/approve/{device_code}")
        assert approve_resp.status == 200

        token_resp = await client.post("/auth/token", json={"device_code": device_code})
        assert token_resp.status == 200


@pytest.mark.asyncio
async def test_device_flow_token_accepted_as_bearer(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        device_code = (await resp.json())["device_code"]
        await client.post(f"/auth/approve/{device_code}")

        token_resp = await client.post("/auth/token", json={"device_code": device_code})
        token = (await token_resp.json())["access_token"]

        agents_resp = await client.get("/agents", headers={"Authorization": f"Bearer {token}"})
        assert agents_resp.status == 200


@pytest.mark.asyncio
async def test_device_flow_disabled_returns_404():
    async def _echo(text, session_id):
        yield text

    config = ACPServerConfig(device_flow_enabled=False, id="test-no-df")
    meta = AgentMetadata(id="operator", name="Operator")
    server = ACPServer(config=config, runners={"operator": _echo}, metadata={"operator": meta})

    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/auth/device")
        assert resp.status == 404


@pytest.mark.asyncio
async def test_device_flow_no_token_returns_401(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.get("/agents")
        assert resp.status == 401


@pytest.mark.asyncio
async def test_post_auth_token_invalid_body_returns_400(df_server):
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/token", data="not-json", headers={"Content-Type": "application/json"})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_post_auth_approve_expired_code_returns_404(df_server):
    import time
    async with TestClient(TestServer(df_server._app)) as client:
        resp = await client.post("/auth/device")
        device_code = (await resp.json())["device_code"]
        # Force expiry
        df_server._device_flow._pending[device_code].expires_at = time.monotonic() - 1
        approve_resp = await client.post(f"/auth/approve/{device_code}")
        assert approve_resp.status == 404


# ---------------------------------------------------------------------------
# Client device_auth() integration test
# ---------------------------------------------------------------------------

from operator_use.acp.client import ACPClient
from operator_use.acp.config import ACPClientConfig


@pytest.mark.asyncio
async def test_client_device_auth_completes(tmp_path):
    """Full device_auth() flow: request code, auto-approve server-side, poll until done."""
    df_server = _make_df_server(tmp_path)
    async with TestClient(TestServer(df_server._app)) as http_client:
        base_url = str(http_client.make_url("/")).rstrip("/")
        cfg = ACPClientConfig(base_url=base_url, agent_id="operator")
        acp = ACPClient(cfg)

        # Simulate: as soon as code is created, auto-approve it (what a human would do)
        original_create = df_server._device_flow.create_code
        def auto_approve_create(verification_uri):
            code = original_create(verification_uri)
            df_server._device_flow.approve(code.device_code)
            return code
        df_server._device_flow.create_code = auto_approve_create

        async with acp:
            token = await acp.device_auth(poll_interval=0.05)

        assert token.startswith("op_")
        assert acp.config.auth_token == token
