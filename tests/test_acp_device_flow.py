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
