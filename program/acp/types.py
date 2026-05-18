from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: float = 5.0


@dataclass
class ACPAgentConfig:
    """Registry entry for a remote ACP agent (from settings.json `acp_agents`)."""
    name: str
    transport: str = 'stdio'   # 'stdio' | 'http' | 'discover'
    command: str | None = None  # stdio: executable name (e.g. 'codex', 'operator')
    args: list[str] = field(default_factory=list)  # stdio: extra CLI args
    url: str | None = None      # http: base URL of the remote agent
