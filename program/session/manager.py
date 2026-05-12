from __future__ import annotations  
from typing import Optional, Dict, List, Any, Set
from datetime import datetime  
from pathlib import Path
from program.session.storage import SessionStorage, FileSessionStorage
from program.session.types import (
    FileEntry, SessionHeader, SessionMessageEntry,ThinkingLevelChangeEntry,
    ModelChangeEntry,CompactionEntry,BranchSummaryEntry,LabelEntry,SessionInfoEntry,
    CustomEntry,CustomMessageEntry,SessionEntry, SessionTreeNode
)
import uuid
import json

CURRENT_SESSION_VERSION = 1  
  
def create_session_id() -> str:  
    """Generate a unique session ID.""" 
    return str(uuid.uuid4())  
  
def generate_id(existing_ids: Set[str]) -> str:
    """Generate a unique short ID (8 hex chars, collision-checked)."""
    for _ in range(100):
        short_id = uuid.uuid4().hex[:8]
        if short_id not in existing_ids:
            return short_id
    return str(uuid.uuid4())

def _infer_entry_type(entry: Dict[str, Any]) -> str:
    """Infer entry type from entry structure."""
    if "message" in entry:
        return "message"
    if "thinkingLevel" in entry:
        return "thinking_level_change"
    if "modelId" in entry:
        return "model_change"
    if "firstKeptEntryId" in entry:
        return "compaction"
    if "fromId" in entry:
        return "branch_summary"
    if "targetId" in entry:
        return "label"
    if "name" in entry and "version" not in entry:
        return "session_info"
    if "customType" in entry and "content" in entry:
        return "custom_message"
    if "customType" in entry:
        return "custom"
    if "version" in entry:
        return "session"
    return "unknown"

def _ensure_type_field(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure entry has type field for serialization."""
    if "type" not in entry:
        entry["type"] = _infer_entry_type(entry)
    return entry

class SessionManager:  
    """Manages conversation sessions as append-only trees stored in JSONL files."""  
      
    def __init__(  
        self,  
        cwd: str,  
        session_dir: str,  
        session_file: Optional[str],  
        persist: bool  
    ):  
        self.session_id: str = ""  
        self.session_file: Optional[str] = session_file  
        self.session_dir: str = session_dir  
        self.cwd: str = cwd  
        self.persist: bool = persist  
        self.flushed: bool = False  
        self.file_entries: List[FileEntry] = []  
        self.by_id: Dict[str, SessionEntry] = {}  
        self.labels_by_id: Dict[str, str] = {}  
        self.label_timestamps_by_id: Dict[str, str] = {}  
        self.leaf_id: Optional[str] = None  
          
        if session_file:  
            self.set_session_file(session_file)  
        else:  
            self.new_session()  
      
    def set_session_file(self, session_file: str) -> None:  
        """Switch to a different session file (used for resume and branching)."""  
        self.session_file = str(Path(session_file).resolve())  
        if Path(self.session_file).exists():  
            self.file_entries = self._load_entries_from_file(self.session_file)  
              
            if not self.file_entries:  
                self.new_session()  
                self.session_file = session_file  
                self._rewrite_file()  
                self.flushed = True  
                return  
              
            header = next((e for e in self.file_entries if e["type"] == "session"), None)  
            self.session_id = header.get("id", create_session_id()) if header else create_session_id()  
              
            self._build_index()  
            self.flushed = True  
        else:  
            explicit_path = self.session_file  
            self.new_session()  
            self.session_file = explicit_path  
      
    def new_session(self) -> None:
        """Create a new session."""
        self.session_id = create_session_id()
        timestamp = datetime.now().isoformat()

        header: SessionHeader = {
            "version": CURRENT_SESSION_VERSION,
            "id": self.session_id,
            "timestamp": timestamp,
            "cwd": self.cwd,
            "parentSession": None
        }

        self.file_entries = [header]  
        self.by_id = {}  
        self.labels_by_id = {}  
        self.label_timestamps_by_id = {}  
        self.leaf_id = None  
        self.flushed = False  
    
    def get_session_name(self) -> Optional[str]:  
        """Get the current session name from the latest session_info entry, if any."""  
        # Walk entries in reverse to find the latest session_info entry.  
        entries = self.get_entries()  
        for entry in reversed(entries):  
            if entry["type"] == "session_info":  
                name = entry.get("name", "").strip()  
                return name if name else None  
        return None

    def create_branched_session(self, leaf_id: str) -> Optional[str]:  
        """  
        Create a new session file containing only the path from root to the specified leaf.  
        Returns the new session file path, or None if not persisting.  
        """  
        previous_session_file = self.session_file  
        path = self.get_branch(leaf_id)  
        if not path:  
            raise ValueError(f"Entry {leaf_id} not found")  
  
        # Filter out LabelEntry from path - we'll recreate them from the resolved map  
        path_without_labels = [e for e in path if e["type"] != "label"]  
  
        new_session_id = create_session_id()  
        timestamp = datetime.now().isoformat()  
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")  
          
        # Determine new file path  
        new_session_file = None  
        if self.persist:  
            new_session_file = str(Path(self.session_dir) / f"{file_timestamp}_{new_session_id}.jsonl")  
  
        header: SessionHeader = {
            "version": CURRENT_SESSION_VERSION,
            "id": new_session_id,
            "timestamp": timestamp,
            "cwd": self.cwd,
            "parentSession": previous_session_file if self.persist else None
        }  
  
        # Collect labels for entries in the path  
        path_entry_ids = {e["id"] for e in path_without_labels}  
        labels_to_write = []  
        for target_id, label in self.labels_by_id.items():  
            if target_id in path_entry_ids:  
                labels_to_write.append({  
                    "target_id": target_id,   
                    "label": label,   
                    "timestamp": self.label_timestamps_by_id.get(target_id)  
                })  
  
        # Build new label entries  
        last_entry_id = path_without_labels[-1]["id"] if path_without_labels else None  
        parent_id = last_entry_id  
        label_entries = []  
          
        # We need a fresh set of IDs for the labels in the new session context  
        existing_ids = set(path_entry_ids)  
        for label_info in labels_to_write:
            label_entry: LabelEntry = {
                "id": generate_id(existing_ids),
                "parentId": parent_id,
                "timestamp": label_info["timestamp"],
                "targetId": label_info["target_id"],
                "label": label_info["label"]
            }
            existing_ids.add(label_entry["id"])
            label_entries.append(label_entry)
            parent_id = label_entry["id"]  
  
        # Update internal state to the new branched session  
        self.file_entries = [header] + path_without_labels + label_entries  
        self.session_id = new_session_id  
        self.session_file = new_session_file  
        self._build_index()  
  
        if self.persist and new_session_file:  
            # Only write immediately if it contains an assistant message  
            has_assistant = any(  
                e["type"] == "message" and e["message"]["role"] == "assistant"   
                for e in self.file_entries  
            )  
            if has_assistant:  
                self._rewrite_file()  
                self.flushed = True  
            else:  
                self.flushed = False  
                  
        return new_session_file
      
    def _load_entries_from_file(self, file_path: str) -> List[FileEntry]:  
        """Load entries from JSONL file."""  
        entries: List[FileEntry] = []  
        path = Path(file_path)  
        if not path.exists():  
            return entries  
          
        content = path.read_text(encoding="utf-8")  
        for line in content.strip().splitlines():  
            if not line.strip():  
                continue  
            try:  
                entry = json.loads(line)  
                entries.append(entry)  
            except json.JSONDecodeError:  
                continue  
        return entries  
      
    def _build_index(self) -> None:  
        """Build the by_id index and labels map."""  
        self.by_id = {}  
        self.labels_by_id = {}  
        self.label_timestamps_by_id = {}  
          
        for entry in self.file_entries:  
            if entry["type"] != "session":  
                self.by_id[entry["id"]] = entry  
                if entry["type"] == "label":  
                    self.labels_by_id[entry["targetId"]] = entry["label"]  
                    self.label_timestamps_by_id[entry["targetId"]] = entry["timestamp"]  
          
        # Set leaf to last entry  
        if self.file_entries:  
            self.leaf_id = self.file_entries[-1]["id"]  
      
    def _append_entry(self, entry: SessionEntry) -> None:  
        """Append entry to internal state and persist."""  
        self.file_entries.append(entry)  
        self.by_id[entry["id"]] = entry  
        self.leaf_id = entry["id"]  
        self._persist(entry)  
      
    def _persist(self, entry: SessionEntry) -> None:
        """Persist entry to file if enabled."""
        if not self.persist or not self.session_file:
            return

        has_assistant = any(
            (_infer_entry_type(e) if "type" not in e else e["type"]) == "message" and e["message"]["role"] == "assistant"
            for e in self.file_entries
        )

        if not has_assistant:
            self.flushed = False
            return

        if not self.flushed:
            # Write all entries on first assistant response
            path = Path(self.session_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(json.dumps(_ensure_type_field(e)) for e in self.file_entries) + "\n"
            path.write_text(content, encoding="utf-8")
            self.flushed = True
        else:
            # Append only the new entry
            path = Path(self.session_file)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_ensure_type_field(entry)) + "\n")  
      
    def _rewrite_file(self) -> None:
        """Rewrite entire file (used for branching/migration)."""
        if not self.persist or not self.session_file:
            return

        path = Path(self.session_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(_ensure_type_field(e)) for e in self.file_entries) + "\n"
        path.write_text(content, encoding="utf-8")  
      
    # =========================================================================  
    # Append Methods  
    # =========================================================================  
      
    def append_message(self, message: Dict[str, Any]) -> str:
        """Append a message as child of current leaf. Returns entry id."""
        entry: SessionMessageEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "message": message
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_thinking_level_change(self, thinking_level: str) -> str:
        """Append a thinking level change. Returns entry id."""
        entry: ThinkingLevelChangeEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "thinkingLevel": thinking_level
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_model_change(self, provider: str, model_id: str) -> str:
        """Append a model change. Returns entry id."""
        entry: ModelChangeEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "modelId": model_id
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Any] = None,
        from_hook: Optional[bool] = None
    ) -> str:
        """Append a compaction entry. Returns entry id."""
        entry: CompactionEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "firstKeptEntryId": first_kept_entry_id,
            "tokensBefore": tokens_before,
            "details": details,
            "fromHook": from_hook
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_branch_summary(
        self,
        summary: str,
        from_id: str,
        details: Optional[Any] = None
    ) -> str:
        """Append a branch summary entry. Returns entry id."""
        entry: BranchSummaryEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "fromId": from_id,
            "details": details
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        """Append a custom entry (for extensions). Returns entry id."""
        entry: CustomEntry = {
            "customType": custom_type,
            "data": data,
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat()
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_custom_message_entry(
        self,
        custom_type: str,
        content: Any,
        display: Optional[str] = None,
        details: Optional[Any] = None
    ) -> str:
        """Append a custom message entry. Returns entry id."""
        entry: CustomMessageEntry = {
            "customType": custom_type,
            "content": content,
            "display": display,
            "details": details,
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat()
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_session_info(self, name: str) -> str:
        """Append a session info entry (display name). Returns entry id."""
        entry: SessionInfoEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "name": name.strip()
        }
        self._append_entry(entry)
        return entry["id"]  
      
    def append_label_change(self, target_id: str, label: Optional[str]) -> str:
        """Set or clear a label on an entry. Returns entry id."""
        if target_id not in self.by_id:
            raise ValueError(f"Entry {target_id} not found")

        entry: LabelEntry = {
            "id": generate_id(set(self.by_id.keys())),
            "parentId": self.leaf_id,
            "timestamp": datetime.now().isoformat(),
            "targetId": target_id,
            "label": label
        }
        self._append_entry(entry)

        if label:
            self.labels_by_id[target_id] = label
            self.label_timestamps_by_id[target_id] = entry["timestamp"]
        else:
            self.labels_by_id.pop(target_id, None)
            self.label_timestamps_by_id.pop(target_id, None)

        return entry["id"]  
      
    # =========================================================================  
    # Tree Traversal  
    # =========================================================================  
      
    def get_leaf_id(self) -> Optional[str]:  
        """Get the current leaf ID."""  
        return self.leaf_id  
      
    def get_leaf_entry(self) -> Optional[SessionEntry]:  
        """Get the current leaf entry."""  
        return self.by_id.get(self.leaf_id) if self.leaf_id else None  
      
    def get_entry(self, entry_id: str) -> Optional[SessionEntry]:  
        """Get a specific entry by ID."""  
        return self.by_id.get(entry_id)  
      
    def get_children(self, parent_id: str) -> List[SessionEntry]:  
        """Get all direct children of an entry."""  
        return [e for e in self.by_id.values() if e.get("parentId") == parent_id]  
      
    def get_label(self, entry_id: str) -> Optional[str]:  
        """Get the label for an entry, if any."""  
        return self.labels_by_id.get(entry_id)  
      
    def get_branch(self, from_id: Optional[str] = None) -> List[SessionEntry]:  
        """Walk from entry to root, returning all entries in path order."""  
        path: List[SessionEntry] = []  
        start_id = from_id or self.leaf_id  
        current = self.by_id.get(start_id) if start_id else None  
          
        while current:  
            path.insert(0, current)  
            parent_id = current.get("parentId")  
            current = self.by_id.get(parent_id) if parent_id else None  
          
        return path  
      
    def get_header(self) -> Optional[SessionHeader]:  
        """Get session header."""  
        for entry in self.file_entries:  
            if entry["type"] == "session":  
                return entry  
        return None  
      
    def get_entries(self) -> List[SessionEntry]:  
        """Get all session entries (excludes header)."""  
        return [e for e in self.file_entries if e["type"] != "session"]  
      
    def get_tree(self) -> List[SessionTreeNode]:  
        """Get the session as a tree structure."""  
        entries = self.get_entries()  
        node_map: Dict[str, SessionTreeNode] = {}  
        roots: List[SessionTreeNode] = []  
          
        # Create nodes with resolved labels  
        for entry in entries:  
            label = self.labels_by_id.get(entry["id"])  
            label_timestamp = self.label_timestamps_by_id.get(entry["id"])  
            node_map[entry["id"]] = {  
                "entry": entry,  
                "children": [],  
                "label": label,  
                "labelTimestamp": label_timestamp  
            }  
          
        # Build tree  
        for entry in entries:  
            node = node_map[entry["id"]]  
            parent_id = entry.get("parentId")  
              
            if parent_id is None or parent_id == entry["id"]:  
                roots.append(node)  
            else:  
                parent = node_map.get(parent_id)  
                if parent:  
                    parent["children"].append(node)  
                else:  
                    roots.append(node)  # Orphan - treat as root  
          
        # Sort children by timestamp  
        def sort_children(nodes: List[SessionTreeNode]) -> None:  
            for node in nodes:  
                node["children"].sort(  
                    key=lambda n: datetime.fromisoformat(n["entry"]["timestamp"])  
                )  
                sort_children(node["children"])  
          
        sort_children(roots)  
        return roots  
      
    def get_session_name(self) -> Optional[str]:  
        """Get the current session name from the latest session_info entry."""  
        entries = self.get_entries()  
        for entry in reversed(entries):  
            if entry["type"] == "session_info":  
                name = entry.get("name", "").strip()  
                return name if name else None  
        return None  
      
    def build_session_context(self) -> Dict[str, Any]:  
        """Build the session context (what gets sent to the LLM)."""  
        entries = self.get_entries()  
        leaf_id = self.leaf_id  
        by_id = self.by_id  
          
        # Find leaf  
        leaf: Optional[SessionEntry] = None  
        if leaf_id:  
            leaf = by_id.get(leaf_id)  
        if not leaf and entries:  
            leaf = entries[-1]  
          
        if not leaf:  
            return {"messages": [], "thinkingLevel": "off", "model": None}  
          
        # Walk from leaf to root  
        path: List[SessionEntry] = []  
        current = leaf  
        while current:  
            path.insert(0, current)  
            parent_id = current.get("parentId")  
            current = by_id.get(parent_id) if parent_id else None  
          
        # Extract settings  
        thinking_level = "off"  
        model: Optional[Dict[str, str]] = None  
        compaction: Optional[CompactionEntry] = None  
          
        for entry in path:  
            if entry["type"] == "thinking_level_change":  
                thinking_level = entry["thinkingLevel"]  
            elif entry["type"] == "model_change":  
                model = {"provider": entry["provider"], "modelId": entry["modelId"]}  
            elif entry["type"] == "message" and entry["message"]["role"] == "assistant":  
                model = {"provider": entry["message"]["provider"], "modelId": entry["message"]["model"]}  
            elif entry["type"] == "compaction":  
                compaction = entry  
          
        # Build messages  
        messages: List[Dict[str, Any]] = []  