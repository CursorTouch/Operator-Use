from __future__ import annotations
from typing import Optional, Dict, List, Any, Set
from datetime import datetime
from pathlib import Path
from program.session.types import (
    FileEntry, SessionHeader, LLMMessageEntry,ThinkingLevelChangeEntry,
    ModelChangeEntry,CompactionSummaryEntry,BranchSummaryEntry,LabelEntry,SessionInfoEntry,
    CustomInfoEntry,CustomMessageEntry,SessionEntry, SessionTreeNode, SessionEntryType
)
from program.session.utils import (
    create_session_id, generate_id,
    get_latest_compaction_entry,
    load_entries_from_file, is_valid_session_file, find_most_recent_session_path,
    is_message_with_content, extract_text_content, get_last_activity_time,
    get_session_modified_date, build_session_info, list_sessions_from_dir,
    SessionListProgress
)

from program.message.types import LLMMessage, Role
from program.llm.types import ThinkingLevel
from program.settings.paths import get_sessions_path
from dataclasses import asdict
import json

CURRENT_SESSION_VERSION = 1

class SessionManager:
    """Manages conversation sessions as append-only trees stored in JSONL files."""

    def __init__(
        self,
        cwd: str,
        session_path: Optional[str],
        persist: bool
    ):
        self.session_id: str = ""
        self.session_path: Optional[str] = session_path or get_sessions_path()
        self.cwd: str = cwd
        self.persist: bool = persist
        self.flushed: bool = False
        self.file_entries: List[FileEntry] = []
        self.by_id: Dict[str, SessionEntry] = {}
        self.labels_by_id: Dict[str, str] = {}
        self.label_timestamps_by_id: Dict[str, str] = {}
        self.leaf_id: Optional[str] = None

        if session_path:
            self.set_session_path(session_path)
        else:
            self.new_session()

    def set_session_path(self, session_path: Path) -> None:
        """Switch to a different session path (used for resume and branching)."""
        self.session_path = str(Path(session_path).resolve())
        if Path(self.session_path).exists():
            self.file_entries = load_entries_from_file(self.session_path)

            if not self.file_entries:
                self.new_session()
                self.session_path = session_path
                self._rewrite_file()
                self.flushed = True
                return

            header = next((e for e in self.file_entries if isinstance(e, SessionHeader)), None)
            self.session_id = header.id if header else create_session_id()

            self._build_index()
            self.flushed = True
        else:
            explicit_path = self.session_path
            self.new_session()
            self.session_path = explicit_path

    def new_session(self) -> None:
        """Create a new session."""
        self.session_id = create_session_id()
        timestamp = datetime.now().isoformat()

        header = SessionHeader(
            id=self.session_id,
            timestamp=timestamp,
            parent_id=None,
            parent_session_path=None,
            version=CURRENT_SESSION_VERSION
        )

        self.file_entries = [header]
        self.by_id = {}
        self.labels_by_id = {}
        self.label_timestamps_by_id = {}
        self.leaf_id = None
        self.flushed = False

    def get_session_name(self) -> Optional[str]:
        """Get the current session name from the latest session_info entry, if any."""
        entries = self.get_entries()
        for entry in reversed(entries):
            if isinstance(entry, SessionInfoEntry):
                name = entry.name.strip() if entry.name else None
                return name if name else None
        return None

    def create_branched_session(self, leaf_id: str) -> Optional[str]:
        """Create a new session file containing only the path from root to the specified leaf."""
        previous_session_path = self.session_path
        path = self.get_branch(leaf_id)
        if not path:
            raise ValueError(f"Entry {leaf_id} not found")

        path_without_labels = [e for e in path if not isinstance(e, LabelEntry)]

        new_session_id = create_session_id()
        timestamp = datetime.now().isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")

        new_session_path = None
        if self.persist:
            new_session_path = str(Path(self.session_path) / f"{file_timestamp}_{new_session_id}.jsonl")

        header = SessionHeader(
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            parent_id=None,
            parent_session_path=previous_session_path if self.persist else None,
        )

        path_entry_ids = {e.id for e in path_without_labels}
        labels_to_write = []
        for target_id, label in self.labels_by_id.items():
            if target_id in path_entry_ids:
                labels_to_write.append({
                    "target_id": target_id,
                    "label": label,
                    "timestamp": self.label_timestamps_by_id.get(target_id)
                })

        last_entry_id = path_without_labels[-1].id if path_without_labels else None
        parent_id = last_entry_id
        label_entries = []

        existing_ids = set(path_entry_ids)
        for label_info in labels_to_write:
            label_entry = LabelEntry(
                id=generate_id(existing_ids),
                parent_id=parent_id,
                timestamp=label_info["timestamp"],
                target_id=label_info["target_id"],
                label=label_info["label"],
            )
            existing_ids.add(label_entry.id)
            label_entries.append(label_entry)
            parent_id = label_entry.id

        self.file_entries = [header] + path_without_labels + label_entries
        self.session_id = new_session_id
        self.session_path = new_session_path
        self._build_index()

        if self.persist and new_session_path:
            has_assistant = any(
                isinstance(e, LLMMessageEntry) and e.message.role == Role.ASSISTANT
                for e in self.file_entries
            )
            if has_assistant:
                self._rewrite_file()
                self.flushed = True
            else:
                self.flushed = False

        return new_session_path

    def _load_entries_from_file(self, file_path: str) -> List[FileEntry]:
        """Load entries from JSONL file."""
        return load_entries_from_file(file_path)

    def _build_index(self) -> None:
        """Build the by_id index and labels map."""
        self.by_id = {}
        self.labels_by_id = {}
        self.label_timestamps_by_id = {}

        for entry in self.file_entries:
            if not isinstance(entry, SessionHeader):
                self.by_id[entry.id] = entry
                if isinstance(entry, LabelEntry):
                    self.labels_by_id[entry.target_id] = entry.label
                    self.label_timestamps_by_id[entry.target_id] = entry.timestamp

        if self.file_entries:
            for entry in reversed(self.file_entries):
                if not isinstance(entry, SessionHeader):
                    self.leaf_id = entry.id
                    break

    def _append_entry(self, entry: SessionEntry) -> None:
        """Append entry to internal state and persist."""
        self.file_entries.append(entry)
        self.by_id[entry.id] = entry
        self.leaf_id = entry.id
        self._persist(entry)

    def _persist(self, entry: SessionEntry) -> None:
        """Persist entry to file if enabled."""
        if not self.persist or not self.session_path:
            return

        has_assistant = any(
            isinstance(e, LLMMessageEntry) and e.message.role == Role.ASSISTANT
            for e in self.file_entries
        )

        if not has_assistant:
            self.flushed = False
            return

        if not self.flushed:
            path = Path(self.session_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(json.dumps(asdict(e)) for e in self.file_entries) + "\n"
            path.write_text(content, encoding="utf-8")
            self.flushed = True
        else:
            path = Path(self.session_path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")

    def _rewrite_file(self) -> None:
        """Rewrite entire file (used for branching/migration)."""
        if not self.persist or not self.session_path:
            return

        path = Path(self.session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(asdict(e)) for e in self.file_entries) + "\n"
        path.write_text(content, encoding="utf-8")

    # =========================================================================
    # Append Methods
    # =========================================================================

    def append_llm_message(self, message: LLMMessage) -> str:
        """Append a message as child of current leaf. Returns entry id."""
        entry = LLMMessageEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            message=message,
        )
        self._append_entry(entry)
        return entry.id

    def append_thinking_level_change(self, thinking_level: ThinkingLevel) -> str:
        """Append a thinking level change. Returns entry id."""
        entry = ThinkingLevelChangeEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            thinking_level=thinking_level,
        )
        self._append_entry(entry)
        return entry.id

    def append_model_change(self, provider: str, model_id: str) -> str:
        """Append a model change. Returns entry id."""
        entry = ModelChangeEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            provider=provider,
            model_id=model_id,
        )
        self._append_entry(entry)
        return entry.id

    def append_label_change(self, target_id: str, label: Optional[str]) -> str:
        """Set or clear a label on an entry. Returns entry id."""
        if target_id not in self.by_id:
            raise ValueError(f"Entry {target_id} not found")

        entry = LabelEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            target_id=target_id,
            label=label,
        )
        self._append_entry(entry)

        if label:
            self.labels_by_id[target_id] = label
            self.label_timestamps_by_id[target_id] = entry.timestamp
        else:
            self.labels_by_id.pop(target_id, None)
            self.label_timestamps_by_id.pop(target_id, None)

        return entry.id

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
        return [e for e in self.by_id.values() if e.parent_id == parent_id]

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
            parent_id = current.parent_id
            current = self.by_id.get(parent_id) if parent_id else None

        return path

    def get_header(self) -> Optional[SessionHeader]:
        """Get session header."""
        for entry in self.file_entries:
            if isinstance(entry, SessionHeader):
                return entry
        return None

    def get_entries(self) -> List[SessionEntry]:
        """Get all session entries (excludes header)."""
        return [e for e in self.file_entries if not isinstance(e, SessionHeader)]

    def get_tree(self) -> List[SessionTreeNode]:
        """Get the session as a tree structure."""
        entries = self.get_entries()
        node_map: Dict[str, SessionTreeNode] = {}
        roots: List[SessionTreeNode] = []

        for entry in entries:
            label = self.labels_by_id.get(entry.id)
            label_timestamp = self.label_timestamps_by_id.get(entry.id)
            node_map[entry.id] = SessionTreeNode(
                entry=entry,
                children=[],
                label=label,
                label_timestamp=label_timestamp
            )

        for entry in entries:
            node = node_map[entry.id]
            parent_id = entry.parent_id

            if parent_id is None or parent_id == entry.id:
                roots.append(node)
            else:
                parent = node_map.get(parent_id)
                if parent:
                    parent.children.append(node)
                else:
                    roots.append(node)

        def sort_children(nodes: List[SessionTreeNode]) -> None:
            for node in nodes:
                node.children.sort(
                    key=lambda n: datetime.fromisoformat(n.entry.timestamp)
                )
                sort_children(node.children)

        sort_children(roots)
        return roots

    # =========================================================================
    # Branching
    # =========================================================================

    def branch(self, branch_from_id: str) -> None:
        """Start a new branch from an earlier entry.

        Moves the leaf pointer to the specified entry. The next append_*() call
        will create a child of that entry, forming a new branch.

        Args:
            branch_from_id: Entry ID to branch from

        Raises:
            ValueError: If entry not found
        """
        if branch_from_id not in self.by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self.leaf_id = branch_from_id

    def reset_leaf(self) -> None:
        """Reset the leaf pointer to null (before any entries).

        The next append_*() call will create a new root entry.
        """
        self.leaf_id = None

    def branch_with_summary(
        self,
        branch_from_id: Optional[str],
        summary: str,
        details: Optional[Any] = None
    ) -> str:
        """Start a new branch with a summary of the abandoned path.

        Args:
            branch_from_id: Entry ID to branch from (None to branch from root)
            summary: Summary text describing the abandoned path
            details: Optional extension-specific metadata

        Returns:
            Entry ID of the branch summary entry

        Raises:
            ValueError: If entry not found
        """
        if branch_from_id is not None and branch_from_id not in self.by_id:
            raise ValueError(f"Entry {branch_from_id} not found")

        self.leaf_id = branch_from_id

        entry = BranchSummaryEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=branch_from_id,
            timestamp=datetime.now().isoformat(),
            from_id=branch_from_id or "root",
            summary=summary,
            details=details,
        )
        self._append_entry(entry)
        return entry.id

    # =========================================================================
    # Static Factory Methods
    # =========================================================================

    @staticmethod
    def create(cwd: str, session_path: Optional[str] = None) -> "SessionManager":
        """Create a new session."""
        if session_path is None:
            session_path = get_sessions_path(cwd)

        manager = SessionManager(cwd, session_path, None, True)
        manager.new_session()
        return manager

    @staticmethod
    def open(path: str, session_path: Optional[str] = None, cwd_override: Optional[str] = None) -> "SessionManager":
        """Open a specific session file."""
        file_path = Path(path)
        entries = load_entries_from_file(str(file_path))

        cwd = cwd_override
        if not cwd and entries:
            header = next((e for e in entries if isinstance(e, SessionHeader)), None)
            if header:
                cwd = header.cwd
        if not cwd:
            cwd = str(Path.cwd())

        if session_path is None:
            session_path = str(file_path.parent)

        manager = SessionManager(cwd, session_path, str(file_path), True)
        return manager

    @staticmethod
    def continueRecent(cwd: str, session_path: Optional[str] = None) -> "SessionManager":
        """Continue the most recent session, or create new if none."""
        if session_path is None:
            session_path = get_sessions_path(cwd)

        most_recent_path = find_most_recent_session_path(session_path)
        if most_recent_path:
            return SessionManager.open(most_recent_path, session_path)

        return SessionManager.create(cwd, session_path)

    @staticmethod
    def inMemory(cwd: Optional[str] = None) -> "SessionManager":
        """Create an in-memory session (no file persistence)."""
        if cwd is None:
            cwd = str(Path.cwd())

        manager = SessionManager(cwd, "", None, False)
        manager.new_session()
        return manager

    @staticmethod
    def forkFrom(source_path: str, target_cwd: str, session_path: Optional[str] = None) -> "SessionManager":
        """Fork a session from another project directory."""
        source_file = Path(source_path)
        if not source_file.exists():
            raise ValueError(f"Source session file not found: {source_path}")

        source_entries = load_entries_from_file(str(source_file))

        if not source_entries:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source_path}")

        header = next((e for e in source_entries if isinstance(e, SessionHeader)), None)
        if not header:
            raise ValueError(f"Cannot fork: source session has no header: {source_path}")

        if session_path is None:
            session_path = get_sessions_path()

        session_path = Path(session_path)
        session_path.mkdir(parents=True, exist_ok=True)

        new_session_id = create_session_id()
        timestamp = datetime.now().isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        new_session_file = str(session_path / f"{file_timestamp}_{new_session_id}.jsonl")

        new_header = SessionHeader(
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            cwd=target_cwd,
            parent_id=None,
            parent_session_path=str(source_path)
        )

        with open(new_session_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(new_header)) + "\n")
            for entry in source_entries:
                if not isinstance(entry, SessionHeader):
                    f.write(json.dumps(asdict(entry)) + "\n")

        return SessionManager.open(new_session_file, session_path, target_cwd)

    @staticmethod
    def list(session_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all sessions for a directory."""
        if session_path is None:
            session_path = get_sessions_path()

        return list_sessions_from_dir(session_path)
