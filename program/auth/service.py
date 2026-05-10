import json
import os
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.oauth import OAuthLoginCallbacks
from program.settings.paths import get_auth_path
from program.auth.types import AuthCredential, OAuthCredential, APICredential
from program.utils import strip_json_comments
from filelock import FileLock

class AuthStore:
    def __init__(self, registry: ProviderRegistry):
        self.store_path = get_auth_path()
        self.registry = registry
        self.data: dict[str, AuthCredential] = self._load()
        self.lock_path = self.store_path.with_suffix(".lock")

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
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:  
        """Save credentials to disk with file locking"""  
        with FileLock(self.lock_path):  
            # Read current file to preserve external edits  
            try:  
                current_content = self.store_path.read_text(encoding="utf-8")  
                current_data = json.loads(current_content) if current_content.strip() else {}  
            except (FileNotFoundError, json.JSONDecodeError):  
                current_data = {}  
              
            # Merge with our data  
            merged_data = {**current_data, **self.data}  
              
            # Write back  
            self.store_path.write_text(  
                json.dumps(merged_data, indent=2),   
                encoding="utf-8"  
            )  
            # Set secure permissions  
            self.store_path.chmod(0o600)  

    def get(self, provider: str) -> AuthCredential | None:
        return self.data.get(provider)
        
    def set(self, provider: str, credential: AuthCredential) -> None:
        self.data[provider] = credential
        self._save()

    async def refresh_oauth_token_with_lock(self, provider: str) -> OAuthCredential | None:  
        """Refresh OAuth token with file locking to prevent race conditions."""  
        oauth_provider = self.registry.get_oauth_provider(provider)  
        if not oauth_provider:  
            return None  
    
        with FileLock(self.lock_path):  
            # Reload data within lock to get latest state  
            current_data = self._load()  
            credential = current_data.get(provider)  
            
            if not isinstance(credential, OAuthCredential):  
                return None  
            
            # Check if another instance already refreshed  
            if not oauth_provider.is_expired(credential):  
                # Another instance refreshed successfully  
                self.data = current_data
                return credential
            
            # Perform refresh  
            try:  
                refreshed_credential = await oauth_provider.refresh_token(credential)  
                
                # Update and save within lock  
                current_data[provider] = refreshed_credential  
                self.data = current_data
                self._save()  
                return refreshed_credential
            except Exception:  
                return None 
        
    async def get_api_key(self, provider: str) -> str | None:
        credential = self.get(provider)

        if credential is None:
            return None

        if isinstance(credential, APICredential):
            return credential.key

        if isinstance(credential, OAuthCredential):
            oauth_provider = self.registry.get_oauth_provider(provider)
            if not oauth_provider:
                return None

            if oauth_provider.is_expired(credential):
                refreshed_credential =await self.refresh_oauth_token_with_lock(provider)
                if refreshed_credential:
                    credential = refreshed_credential
                else:
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


