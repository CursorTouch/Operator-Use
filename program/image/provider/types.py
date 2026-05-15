from __future__ import annotations

from dataclasses import dataclass

from program.llm.types import AuthType


@dataclass
class Provider:
    name: str
    api: str
    base_url: str
    auth_type: AuthType = AuthType.ApiKey
