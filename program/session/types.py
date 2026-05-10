
from dataclasses import dataclass  
from typing import TypeVar

T = TypeVar('T')  
  
@dataclass  
class LockResult:  
    result: T  
    next: str | None = None  