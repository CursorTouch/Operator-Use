from dataclasses import dataclass, field
from typing import Optional, Type
from program.llm.api.base import BaseAPI
from program.llm.types import AuthType, Options

__all__ = ["AuthType", "APIProvider", "OAuthProvider"]


@dataclass
class APIProvider:
    name: str
    api: Type[BaseAPI]
    options: Options
    auth_type: AuthType = AuthType.ApiKey

    def get_api_key(self) -> str | None:
        return self.options.api_key

    def get_base_url(self) -> str | None:
        return self.options.base_url


@dataclass
class OAuthProvider:
    name: str
    api: Type[BaseAPI]
    options: Options
    client_id: str
    client_secret: str
    authorization_url: str
    token_url: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=list)
    auth_type: AuthType = AuthType.OAuth

    def get_base_url(self) -> str | None:
        return self.options.base_url
