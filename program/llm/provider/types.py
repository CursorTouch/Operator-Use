from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Type, Union, TYPE_CHECKING

from dataclasses import field
from program.llm.api.base import BaseAPI
from program.llm.types import AuthType, Options, TransportType

if TYPE_CHECKING:
    from program.llm.provider.oauth.types import OAuthCredential, OAuthLoginCallbacks, AbortSignal

__all__ = ["AuthType", "APIProvider", "OAuthProvider"]


@dataclass
class OAuthProvider(ABC):
    id: str
    name: str
    auth_type: AuthType = AuthType.OAuth
    uses_callback_server: bool = False

    @property
    @abstractmethod
    def api(self) -> Type[BaseAPI]: ...

    @abstractmethod
    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential: ...

    @abstractmethod
    async def refresh_token(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential: ...

    @abstractmethod
    async def logout(self, credential: OAuthCredential) -> None: ...

    @abstractmethod
    def get_api_key(self, credential: OAuthCredential) -> str: ...

    @abstractmethod
    async def validate(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> bool: ...

    def is_expired(self, credential: OAuthCredential) -> bool:
        return int(time.time() * 1000) + 30_000 >= credential.expires

    async def ensure_fresh(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential:
        if self.is_expired(credential):
            return await self.refresh_token(credential=credential, signal=signal)
        return credential


@dataclass
class APIProvider:
    name: str
    api: Union[str, Type[BaseAPI]]
    options: Options
    auth_type: AuthType = AuthType.ApiKey
    supported_transports: list[TransportType] = field(default_factory=lambda: [TransportType.HTTP])

    def get_api_key(self) -> Optional[str]:
        return self.options.api_key

    def get_base_url(self) -> Optional[str]:
        return self.options.base_url
