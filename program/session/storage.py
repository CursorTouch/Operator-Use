from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Any
from program.settings.paths import get_sessions_path
from pathlib import Path
import json

class SessionStorage(ABC):  
    """Abstract storage backend for session JSONL files."""  
      
    @abstractmethod  
    def load_entries(self) -> List[Dict[str, Any]]:  
        """Load all entries from the JSONL file."""  
        pass  
      
    @abstractmethod  
    def append_entry(self, entry: Dict[str, Any]) -> None:  
        """Append a single entry to the JSONL file."""  
        pass  
      
    @abstractmethod  
    def rewrite_file(self, entries: List[Dict[str, Any]]) -> None:  
        """Rewrite the entire file with new entries."""  
        pass  
  
class FileSessionStorage(SessionStorage):  
    """File-based storage for JSONL session files."""  
      
    def __init__(self, sessions_path: Path|None=None):  
        self.sessions_path = sessions_path or get_sessions_path()  
        self._ensure_parent_dir()  
      
    def _ensure_parent_dir(self) -> None:  
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)  
      
    def load_entries(self) -> List[Dict[str, Any]]:  
        if not self.sessions_path.exists():  
            return []  
          
        entries = []  
        with open(self.sessions_path, 'r', encoding='utf-8') as f:  
            for line in f:  
                line = line.strip()  
                if not line:  
                    continue  
                try:  
                    entries.append(json.loads(line))  
                except json.JSONDecodeError:  
                    continue  
          
        # Validate session header  
        if entries and (entries[0].get('type') != 'session' or 'id' not in entries[0]):  
            return []  
          
        return entries  
      
    def append_entry(self, entry: Dict[str, Any]) -> None:  
        self._ensure_parent_dir()  
        with open(self.sessions_path, 'a', encoding='utf-8') as f:  
            f.write(json.dumps(entry) + '\n')  
      
    def rewrite_file(self, entries: List[Dict[str, Any]]) -> None:  
        self._ensure_parent_dir()  
        with open(self.sessions_path, 'w', encoding='utf-8') as f:  
            for entry in entries:  
                f.write(json.dumps(entry) + '\n')  
  
class InMemorySessionStorage(SessionStorage):  
    """In-memory storage for testing."""  
      
    def __init__(self):  
        self.entries: List[Dict[str, Any]] = []  
      
    def load_entries(self) -> List[Dict[str, Any]]:  
        return list(self.entries)  
      
    def append_entry(self, entry: Dict[str, Any]) -> None:  
        self.entries.append(entry)  
      
    def rewrite_file(self, entries: List[Dict[str, Any]]) -> None:  
        self.entries = list(entries)  