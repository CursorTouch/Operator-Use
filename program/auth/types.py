from program.llm.types import AuthType
from dataclasses import dataclass,field
from typing import TypeVar

@dataclass
class OAuthCredential:
    type:AuthType=field(default_factory=lambda: AuthType.OAuth,init=False)
    access:str
    refresh:str
    expires:int # Unix timestamp in milliseconds
    account_id: str | None = None

@dataclass
class APICredential:
    type:AuthType=field(default_factory=lambda: AuthType.APIKey,init=False)
    key:str

AuthCredential=OAuthCredential|APICredential

T = TypeVar('T')  
  
@dataclass  
class LockResult:  
    result: T  
    next: str | None = None  
    
    