from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MCPServerConfig:
    name: str
    transport: str = 'stdio'          # 'stdio' | 'http' | 'sse'
    command: str | None = None        # stdio: executable (e.g. 'npx', 'uvx')
    args: list[str] = field(default_factory=list)
    url: str | None = None            # http/sse transport URL
    env: dict[str, str] = field(default_factory=dict)
    auth_token: str | None = None     # Bearer token for HTTP auth
