import json
import os
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.oauth import OAuthLoginCallbacks
from program.config import get_auth_path
from program.auth.types import AuthCredential, OAuthCredential, APICredential
from program.utils import strip_json_comments

class AuthStore:
    def __init__(self, registry: ProviderRegistry):
        self.store_path = get_auth_path()
        self.registry = registry
        self.data: dict[str, AuthCredential] = self._load()

    def _load(self) -> dict[str, AuthCredential]:
        try:
            content = self.store_path.read_text(encoding="utf-8")
            clean_content = strip_json_comments(content)
            raw_data = json.loads(clean_content)
            data: dict[str, AuthCredential] = {}
            for k, v in raw_data.items():
                if "refresh" in v:
                    data[k] = OAuthCredential(
                        access=v.get("access", ""),
                        refresh=v.get("refresh", ""),
                        expires=v.get("expires", 0),
                        account_id=v.get("account_id")
                    )
                else:
                    data[k] = APICredential(key=v.get("key", ""))
            return data
        except Exception:
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        raw_data = {}
        for k, v in self.data.items():
            if isinstance(v, OAuthCredential):
                raw_data[k] = {
                    "access": v.access,
                    "refresh": v.refresh,
                    "expires": v.expires,
                    "account_id": getattr(v, "account_id", None)
                }
            elif isinstance(v, APICredential):
                raw_data[k] = {
                    "key": getattr(v, "key", "")
                }
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2)

    def get(self, provider: str) -> AuthCredential | None:
        return self.data.get(provider)
        
    def set(self, provider: str, credential: AuthCredential) -> None:
        self.data[provider] = credential
        self._save()
        
    async def get_api_key(self, provider: str) -> str | None:
        credential = self.get(provider)

        if isinstance(credential, APICredential):
            return credential.key

        if isinstance(credential, OAuthCredential):
            oauth_provider = self.registry.get_oauth_provider(provider)
            if not oauth_provider:
                return None

            if oauth_provider.is_expired(credential):
                try:
                    credential = await oauth_provider.refresh_token(credential)
                    self.set(provider, credential)
                except Exception:
                    # If refresh fails, they need to log in again
                    return None
            return oauth_provider.get_api_key(credential)
        
        return None
    
    async def login(self, provider: str, callbacks: OAuthLoginCallbacks):
        if oauth_provider := self.registry.get_oauth_provider(provider):
            credential = await oauth_provider.login(callbacks=callbacks)
            self.data[provider] = credential
            self._save()

    async def logout(self, provider: str):
        if oauth_provider := self.registry.get_oauth_provider(provider):
            if credential := self.get(provider):
                if isinstance(credential, OAuthCredential):
                    await oauth_provider.logout(credential=credential)
                if provider in self.data:
                    del self.data[provider]
                    self._save()


