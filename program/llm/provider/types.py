from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Type, Union, TYPE_CHECKING

from program.llm.api.base import BaseAPI
from program.llm.types import AuthType, Options

if TYPE_CHECKING:
    from program.llm.provider.oauth.types import OAuthCredentials, OAuthLoginCallbacks, AbortSignal

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
    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...

    @abstractmethod
    async def refresh_token(self, credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> OAuthCredentials: ...

    @abstractmethod
    async def logout(self, credentials: OAuthCredentials) -> None: ...

    @abstractmethod
    def get_api_key(self, credentials: OAuthCredentials) -> str: ...

    @abstractmethod
    async def validate(self, credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> bool: ...

    def is_expired(self, credentials: OAuthCredentials) -> bool:
        return int(time.time() * 1000) + 30_000 >= credentials.expires

    async def ensure_fresh(self, credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> OAuthCredentials:
        if self.is_expired(credentials):
            return await self.refresh_token(credentials, signal=signal)
        return credentials


@dataclass
class APIProvider:
    name: str
    api: Union[str, Type[BaseAPI]]
    options: Options
    auth_type: AuthType = AuthType.ApiKey

    def get_api_key(self) -> Optional[str]:
        return self.options.api_key

    def get_base_url(self) -> Optional[str]:
        return self.options.base_url
