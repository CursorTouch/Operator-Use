from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Type, Union, TYPE_CHECKING

from program.inference.types import AuthType, Options, Transport

if TYPE_CHECKING:
    from program.inference.api.llm.base import BaseLLMAPI
    from program.inference.provider.oauth.types import OAuthCredential, OAuthLoginCallbacks, AbortSignal

__all__ = ["AuthType", "APIProvider", "OAuthProvider", "ImageProvider"]


@dataclass
class OAuthProvider(ABC):
    id: str
    name: str
    auth_type: AuthType = AuthType.OAuth
    uses_callback_server: bool = False

    @property
    @abstractmethod
    def api(self) -> Type["BaseLLMAPI"]: ...

    @abstractmethod
    async def login(self, callbacks: "OAuthLoginCallbacks") -> "OAuthCredential": ...

    @abstractmethod
    async def refresh_token(self, credential: "OAuthCredential", signal: Optional["AbortSignal"] = None) -> "OAuthCredential": ...

    @abstractmethod
    async def logout(self, credential: "OAuthCredential") -> None: ...

    @abstractmethod
    def get_api_key(self, credential: "OAuthCredential") -> str: ...

    @abstractmethod
    async def validate(self, credential: "OAuthCredential", signal: Optional["AbortSignal"] = None) -> bool: ...

    def is_expired(self, credential: "OAuthCredential") -> bool:
        return int(time.time() * 1000) + 30_000 >= credential.expires

    async def ensure_fresh(self, credential: "OAuthCredential", signal: Optional["AbortSignal"] = None) -> "OAuthCredential":
        if self.is_expired(credential):
            return await self.refresh_token(credential=credential, signal=signal)
        return credential


@dataclass
class APIProvider:
    id: str
    name: str
    api: Union[str, Type["BaseLLMAPI"]]
    options: Options
    auth_type: AuthType = AuthType.ApiKey
    supported_transports: list[Transport] = field(default_factory=lambda: [Transport.HTTP])

    def get_api_key(self) -> Optional[str]:
        return self.options.api_key

    def get_base_url(self) -> Optional[str]:
        return self.options.base_url


@dataclass
class ImageProvider:
    name: str
    api: str
    base_url: str
    auth_type: AuthType = AuthType.ApiKey
