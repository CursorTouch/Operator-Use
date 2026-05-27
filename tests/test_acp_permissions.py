from __future__ import annotations

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption

from operator_use.acp.client import OperatorACPClient


def make_opt(option_id: str, kind: str, name: str = '') -> PermissionOption:
    return PermissionOption(option_id=option_id, kind=kind, name=name or kind)


@pytest.fixture
def client() -> OperatorACPClient:
    return OperatorACPClient()


@pytest.mark.asyncio
async def test_prefers_allow_always_over_allow_once(client: OperatorACPClient) -> None:
    opts = [
        make_opt('once', 'allow_once'),
        make_opt('always', 'allow_always'),
        make_opt('deny', 'reject_once'),
    ]
    resp = await client.request_permission(opts, 'sid', None)
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == 'always'


@pytest.mark.asyncio
async def test_falls_back_to_allow_once_when_no_allow_always(client: OperatorACPClient) -> None:
    opts = [
        make_opt('once', 'allow_once'),
        make_opt('deny', 'reject_once'),
    ]
    resp = await client.request_permission(opts, 'sid', None)
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == 'once'


@pytest.mark.asyncio
async def test_deny_when_only_reject_options(client: OperatorACPClient) -> None:
    opts = [make_opt('deny', 'reject_once')]
    resp = await client.request_permission(opts, 'sid', None)
    assert isinstance(resp.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_deny_when_empty_options(client: OperatorACPClient) -> None:
    resp = await client.request_permission([], 'sid', None)
    assert isinstance(resp.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_response_serialises_to_valid_acp_json(client: OperatorACPClient) -> None:
    opts = [make_opt('always', 'allow_always')]
    resp = await client.request_permission(opts, 'sid', None)
    payload = resp.model_dump(mode='json', by_alias=True, exclude_none=True)
    assert payload == {'outcome': {'optionId': 'always', 'outcome': 'selected'}}
