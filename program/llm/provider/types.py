from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Type, Union

from program.llm.api.base import BaseAPI
from program.llm.provider.oauth.types import OAuthCredentials, OAuthLoginCallbacks
from program.llm.types import AuthType, Options

__all__ = ["AuthType", "APIProvider", "OAuthProvider"]


class OAuthProvider(ABC):
    auth_type: AuthType = AuthType.OAuth

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def uses_callback_server(self) -> bool:
        return False

    @abstractmethod
    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...

    @abstractmethod
    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials: ...

    @abstractmethod
    async def logout(self, credentials: OAuthCredentials) -> None: ...

    @abstractmethod
    def get_api_key(self, credentials: OAuthCredentials) -> str: ...

    @abstractmethod
    async def validate(self, credentials: OAuthCredentials) -> bool: ...

    @property
    @abstractmethod
    def api(self) -> Type[BaseAPI]: ...

    def is_expired(self, credentials: OAuthCredentials) -> bool:
        return int(time.time() * 1000) + 30_000 >= credentials.expires

    async def ensure_fresh(self, credentials: OAuthCredentials) -> OAuthCredentials:
        if self.is_expired(credentials):
            return await self.refresh_token(credentials)
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
