from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

__all__ = ["OAuthCredentials", "OAuthPrompt", "OAuthAuthInfo", "OAuthLoginCallbacks"]


@dataclass
class OAuthCredentials:
    access: str
    refresh: str
    expires: int  # Unix timestamp in milliseconds
    account_id: str


@dataclass
class OAuthPrompt:
    message: str
    placeholder: str = ""
    allow_empty: bool = False


@dataclass
class OAuthAuthInfo:
    url: str
    instructions: str = ""


@dataclass
class OAuthLoginCallbacks:
    on_auth: Callable[[OAuthAuthInfo], None]
    on_prompt: Callable[[OAuthPrompt], Awaitable[str]]
    on_progress: Optional[Callable[[str], None]] = None
    on_manual_code_input: Optional[Callable[[], Awaitable[str]]] = None
