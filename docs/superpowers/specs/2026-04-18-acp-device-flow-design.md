# ACP Device Flow (OAuth 2.0 Device Authorization Grant)

**Date:** 2026-04-18  
**Status:** Approved

## Problem

Connecting a remote ACP client to an Operator ACP server currently requires manually copying a bearer token into both machines' config. The Device Authorization Grant (RFC 8628) allows a human to approve a new client connection via a browser instead — no token copying needed.

## Goals

- Remote device (Machine B) can connect to Operator ACP server (Machine A) without pre-shared tokens
- Human approves the connection via a local web page on Machine A
- Approved tokens persist across restarts and never expire
- Existing bearer token auth is completely unchanged

## Non-Goals

- Token expiry / rotation
- Telegram/Discord/CLI approval (web page only)
- Multiple approval pages or OAuth provider integration

## Architecture

### New File: `operator_use/acp/device_flow.py`

Manages all device flow state:

- Generates `device_code` (opaque, internal) and `user_code` (short, human-typeable e.g. `KQBG-MDJX`)
- Stores pending codes in memory with expiry (10 minutes to approve)
- Stores approved tokens in memory + persists to `.operator_use/acp_tokens.json`
- Loads persisted tokens on startup
- Validates tokens (called by auth middleware)

### Modified: `operator_use/acp/server.py`

Three new endpoints (only active when `device_flow_enabled: True`):

| Endpoint | Method | Description |
|---|---|---|
| `/auth/device` | POST | Client requests a device code. Returns `user_code`, `verification_uri`, `device_code`, `interval` |
| `/auth/token` | POST | Client polls with `device_code`. Returns `access_token` when approved, `202` while pending |
| `/auth/approve` | GET | Browser page showing pending codes with Approve button |

The auth middleware is extended to also validate device-flow-issued tokens (looked up from `DeviceFlowManager`).

### Modified: `operator_use/acp/client.py`

New `device_auth()` method:

```python
async def device_auth(self) -> str:
    # 1. POST /auth/device → get user_code + verification_uri
    # 2. Print: "Visit <verification_uri> and enter <user_code>"
    # 3. Poll POST /auth/token every `interval` seconds
    # 4. On success: set self.config.auth_token = access_token and return it
```

### Modified: `operator_use/acp/config.py`

```python
@dataclass
class ACPServerConfig:
    ...
    device_flow_enabled: bool = False
```

## Data Flow

```
Machine B (client)                    Machine A (server)
─────────────────                    ──────────────────
POST /auth/device          ────►     Generate device_code + user_code
                           ◄────     {user_code, verification_uri, device_code, interval: 5}

Print: "Visit http://...:8765/auth/approve and enter KQBG-MDJX"

Poll POST /auth/token       ────►     Pending → 202
(every 5 seconds)

                                      Human opens browser → sees code → clicks Approve

Poll POST /auth/token       ────►     Approved
                            ◄────     {access_token: "op_abc123..."}

Use token on all requests   ────►     Auth middleware validates ✓
```

## Token Persistence

File: `.operator_use/acp_tokens.json`

```json
{
  "op_abc123...": {
    "approved_at": "2026-04-18T10:00:00"
  }
}
```

- Loaded on `DeviceFlowManager` init
- Written immediately on approval
- Tokens never expire
- Deleted manually by removing from file or future revoke endpoint

## Approval Web Page

Served at `GET /auth/approve`. Plain HTML — no framework needed:

- Lists all pending codes with their `user_code` and time remaining
- Each has an Approve button (POST to `/auth/approve/{device_code}`)
- If no pending codes: shows "No pending requests"

## Files Changed

| File | Change |
|---|---|
| `operator_use/acp/device_flow.py` | New — all device flow logic |
| `operator_use/acp/server.py` | Add 3 endpoints + extend auth middleware |
| `operator_use/acp/client.py` | Add `device_auth()` method |
| `operator_use/acp/config.py` | Add `device_flow_enabled: bool = False` |
| `operator_use/acp/models.py` | Add `DeviceCodeResponse`, `TokenResponse` models |
| `.operator_use/acp_tokens.json` | Runtime — created on first approval |
