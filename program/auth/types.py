from program.inference.types import AuthType
from dataclasses import dataclass, field
from typing import Generic, TypeVar, Optional, Literal


@dataclass
class OAuthCredential:
    type: AuthType = field(default_factory=lambda: AuthType.OAuth, init=False)
    access: str = ""
    refresh: str = ""
    expires: int = 0  # Unix timestamp in milliseconds
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class APICredential:
    type: AuthType = field(default_factory=lambda: AuthType.ApiKey, init=False)
    key: str = ""


AuthCredential = OAuthCredential | APICredential


@dataclass
class AuthStatus:
    configured: bool
    source: Optional[Literal["stored", "runtime", "env"]] = None
    label: Optional[str] = None


T = TypeVar('T')


@dataclass
class LockResult(Generic[T]):
    result: T
    next: str | None = None
