from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from program.inference.provider.types import AuthType
from program.auth.types import OAuthCredential


__all__ = ["OAuthCredential", "OAuthPrompt", "OAuthAuthInfo", "OAuthLoginCallbacks", "AbortSignal"]


AbortSignal = asyncio.Event


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
    signal: Optional[AbortSignal] = None
