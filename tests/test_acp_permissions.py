from __future__ import annotations

import asyncio

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption

from operator_use.acp.client import OperatorACPClient
from operator_use.bus.service import Bus
from operator_use.bus.types import IncomingMessage, TextPart
from operator_use.gateway.service import (
    _PERMISSION_FUTURES,
    register_permission_future,
)


def make_opt(option_id: str, kind: str, name: str = '') -> PermissionOption:
    return PermissionOption(option_id=option_id, kind=kind, name=name or kind)


def make_tool_call(name: str = 'Bash', raw_input: object = None):
    class _TC:
        tool_name = name
        raw_input = None
    t = _TC()
    t.raw_input = raw_input or {'command': 'ls -la'}
    return t


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


# ── Interactive routing (bus + future intercept) ──────────────────────────────

CHANNEL = 'test-channel'
CHAT_ID = 'chat-123'


def make_interactive_client(bus: Bus) -> OperatorACPClient:
    return OperatorACPClient(bus=bus, channel=CHANNEL, chat_id=CHAT_ID)


async def _reply_after(bus: Bus, text: str, delay: float = 0.05) -> None:
    """Simulate the user replying on the test channel after a short delay."""
    await asyncio.sleep(delay)
    # Normally the gateway._handle_incoming would resolve the future.
    # Here we invoke the same lookup directly to stay independent of Gateway.
    fut = _PERMISSION_FUTURES.get((CHANNEL, CHAT_ID))
    if fut and not fut.done():
        fut.set_result(IncomingMessage(
            channel=CHANNEL, chat_id=CHAT_ID,
            parts=[TextPart(content=text)],
        ))


@pytest.mark.asyncio
async def test_interactive_numeric_reply_selects_option() -> None:
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [
        make_opt('always', 'allow_always', 'Always Allow'),
        make_opt('once', 'allow_once', 'Allow'),
        make_opt('deny', 'reject_once', 'Reject'),
    ]
    asyncio.create_task(_reply_after(bus, '2'))  # "Allow"
    resp = await client.request_permission(opts, 'sid', make_tool_call())
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == 'once'


@pytest.mark.asyncio
async def test_interactive_numeric_reply_deny() -> None:
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [
        make_opt('always', 'allow_always', 'Always Allow'),
        make_opt('once', 'allow_once', 'Allow'),
        make_opt('deny', 'reject_once', 'Reject'),
    ]
    asyncio.create_task(_reply_after(bus, '3'))  # "Reject"
    resp = await client.request_permission(opts, 'sid', make_tool_call())
    assert isinstance(resp.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_interactive_text_allow_reply() -> None:
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [
        make_opt('always', 'allow_always', 'Always Allow'),
        make_opt('once', 'allow_once', 'Allow'),
    ]
    asyncio.create_task(_reply_after(bus, 'allow_once'))
    resp = await client.request_permission(opts, 'sid', make_tool_call())
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == 'once'


@pytest.mark.asyncio
async def test_interactive_deny_word() -> None:
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [make_opt('once', 'allow_once', 'Allow')]
    asyncio.create_task(_reply_after(bus, 'deny'))
    resp = await client.request_permission(opts, 'sid', make_tool_call())
    assert isinstance(resp.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_interactive_publishes_outgoing_prompt() -> None:
    """Verify request_permission sends a human-readable message to the bus."""
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [make_opt('once', 'allow_once', 'Allow')]
    asyncio.create_task(_reply_after(bus, '1'))
    await client.request_permission(opts, 'sid', make_tool_call('Bash', {'command': 'rm -rf /tmp/x'}))
    out = bus._outgoing.get_nowait()
    text = '\n'.join(p.content for p in out.parts if isinstance(p, TextPart))
    assert '[ACP Permission Request]' in text
    assert 'Bash' in text


@pytest.mark.asyncio
async def test_interactive_future_cleaned_up_after_response() -> None:
    bus = Bus()
    client = make_interactive_client(bus)
    opts = [make_opt('once', 'allow_once', 'Allow')]
    asyncio.create_task(_reply_after(bus, '1'))
    await client.request_permission(opts, 'sid', make_tool_call())
    assert (CHANNEL, CHAT_ID) not in _PERMISSION_FUTURES


@pytest.mark.asyncio
async def test_gateway_handle_incoming_resolves_future() -> None:
    """Gateway._handle_incoming should resolve a pending permission future and
    NOT route the message to the session manager."""
    from operator_use.gateway.service import Gateway

    class _FakeRuntime:
        bus = Bus()
        settings_manager = None

    gw = Gateway(_FakeRuntime())  # type: ignore[arg-type]
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    register_permission_future(CHANNEL, CHAT_ID, fut)

    msg = IncomingMessage(channel=CHANNEL, chat_id=CHAT_ID, parts=[TextPart(content='2')])
    await gw._handle_incoming(msg)

    assert fut.done()
    assert fut.result() is msg
    assert (CHANNEL, CHAT_ID) not in _PERMISSION_FUTURES
