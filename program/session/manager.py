from __future__ import annotations
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path
from program.session.types import (
    FileEntry, SessionHeader, LLMMessageEntry, ThinkingLevelChangeEntry,
    ModelChangeEntry, CompactionSummaryEntry, BranchSummaryEntry, LabelEntry,
    SessionInfoEntry, CustomInfoEntry, CustomMessageEntry, SessionEntry,
    SessionTreeNode, SessionContext, SessionEntryType,
)
from program.session.utils import (
    create_session_id, generate_id,
    get_default_session_dir, get_latest_compaction_entry,
    load_entries_from_file, is_valid_session_file, find_most_recent_session_file,
    is_message_with_content, extract_text_content, get_last_activity_time,
    get_session_modified_date, build_session_info, list_sessions_from_dir,
    list_all_sessions, build_session_context, SessionListProgress,
)
from program.message.types import LLMMessage, Role
from program.llm.types import ThinkingLevel
from dataclasses import asdict
import json

CURRENT_SESSION_VERSION = 1


class SessionManager:
    """Manages conversation sessions as append-only trees stored in JSONL files."""

    def __init__(
        self,
        cwd: str,
        session_dir: str,
        session_file: Optional[str],
        persist: bool,
    ):
        self.cwd: str = cwd
        self.session_dir: str = session_dir
        self.session_id: str = ""
        self.session_file: Optional[str] = None  # specific JSONL file
        self.persist: bool = persist
        self.flushed: bool = False
        self.file_entries: List[FileEntry] = []
        self.by_id: Dict[str, SessionEntry] = {}
        self.labels_by_id: Dict[str, str] = {}
        self.label_timestamps_by_id: Dict[str, str] = {}
        self.leaf_id: Optional[str] = None

        if persist and session_dir:
            Path(session_dir).mkdir(parents=True, exist_ok=True)

        if session_file:
            self.set_session_file(session_file)
        else:
            self.new_session()

    def set_session_file(self, session_file: str) -> None:
        """Switch to a different session file (used for resume and branching)."""
        self.session_file = str(Path(session_file).resolve())
        if Path(self.session_file).exists():
            self.file_entries = load_entries_from_file(self.session_file)

            if not self.file_entries:
                explicit = self.session_file
                self.new_session()
                self.session_file = explicit
                self._rewrite_file()
                self.flushed = True
                return

            header = next((e for e in self.file_entries if isinstance(e, SessionHeader)), None)
            self.session_id = header.id if header else create_session_id()

            self._build_index()
            self.flushed = True
        else:
            explicit = self.session_file
            self.new_session()
            self.session_file = explicit

    def new_session(self) -> Optional[str]:
        """Create a new session, returning the new file path (or None for in-memory)."""
        self.session_id = create_session_id()
        timestamp = datetime.now().isoformat()

        header = SessionHeader(
            id=self.session_id,
            timestamp=timestamp,
            parent_id=None,
            cwd=self.cwd,
            parent_session_file=None,
            version=CURRENT_SESSION_VERSION,
        )

        self.file_entries = [header]
        self.by_id = {}
        self.labels_by_id = {}
        self.label_timestamps_by_id = {}
        self.leaf_id = None
        self.flushed = False
        self.session_file = None

        if self.persist and self.session_dir:
            file_timestamp = timestamp.replace(":", "-").replace(".", "-")
            self.session_file = str(
                Path(self.session_dir) / f"{file_timestamp}_{self.session_id}.jsonl"
            )

        return self.session_file

    def get_session_name(self) -> Optional[str]:
        """Get the current session name from the latest session_info entry, if any."""
        for entry in reversed(self.get_entries()):
            if isinstance(entry, SessionInfoEntry):
                return entry.name.strip() if entry.name else None
        return None

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _build_index(self) -> None:
        self.by_id = {}
        self.labels_by_id = {}
        self.label_timestamps_by_id = {}
        self.leaf_id = None

        for entry in self.file_entries:
            if isinstance(entry, SessionHeader):
                continue
            self.by_id[entry.id] = entry
            self.leaf_id = entry.id

            if isinstance(entry, LabelEntry):
                if entry.label:
                    self.labels_by_id[entry.target_id] = entry.label
                    self.label_timestamps_by_id[entry.target_id] = entry.timestamp
                else:
                    self.labels_by_id.pop(entry.target_id, None)
                    self.label_timestamps_by_id.pop(entry.target_id, None)

    def _append_entry(self, entry: SessionEntry) -> None:
        self.file_entries.append(entry)
        self.by_id[entry.id] = entry
        self.leaf_id = entry.id
        self._persist(entry)

    def _persist(self, entry: SessionEntry) -> None:
        if not self.persist or not self.session_file:
            return

        has_assistant = any(
            isinstance(e, LLMMessageEntry) and e.message.role == Role.ASSISTANT
            for e in self.file_entries
        )

        if not has_assistant:
            self.flushed = False
            return

        if not self.flushed:
            path = Path(self.session_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(json.dumps(asdict(e)) for e in self.file_entries) + "\n"
            path.write_text(content, encoding="utf-8")
            self.flushed = True
        else:
            with open(self.session_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")

    def _rewrite_file(self) -> None:
        if not self.persist or not self.session_file:
            return
        path = Path(self.session_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(asdict(e)) for e in self.file_entries) + "\n"
        path.write_text(content, encoding="utf-8")

    # =========================================================================
    # Append Methods
    # =========================================================================

    def append_llm_message(self, message: LLMMessage) -> str:
        """Append an LLM message as child of current leaf. Returns entry id."""
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

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[Any] = None,
    ) -> str:
        """Append a compaction summary entry. Returns entry id."""
        entry = CompactionSummaryEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            details=details,
        )
        self._append_entry(entry)
        return entry.id

    def append_session_info(self, name: str) -> str:
        """Append a session info entry (e.g. display name). Returns entry id."""
        entry = SessionInfoEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            name=name.strip(),
        )
        self._append_entry(entry)
        return entry.id

    def append_custom_entry(self, custom_type: str, data: Optional[Any] = None) -> str:
        """Append an extension state entry (not sent to LLM). Returns entry id."""
        entry = CustomInfoEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            custom_type=custom_type,
            data=data,
        )
        self._append_entry(entry)
        return entry.id

    def append_custom_message_entry(
        self,
        custom_type: str,
        content: str,
        display: bool,
        details: Optional[Any] = None,
    ) -> str:
        """Append an extension message that participates in LLM context. Returns entry id."""
        entry = CustomMessageEntry(
            id=generate_id(set(self.by_id.keys())),
            parent_id=self.leaf_id,
            timestamp=datetime.now().isoformat(),
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
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
    # Read / Query Methods
    # =========================================================================

    def get_leaf_id(self) -> Optional[str]:
        return self.leaf_id

    def get_leaf_entry(self) -> Optional[SessionEntry]:
        return self.by_id.get(self.leaf_id) if self.leaf_id else None

    def get_entry(self, entry_id: str) -> Optional[SessionEntry]:
        return self.by_id.get(entry_id)

    def get_children(self, parent_id: str) -> List[SessionEntry]:
        return [e for e in self.by_id.values() if e.parent_id == parent_id]

    def get_label(self, entry_id: str) -> Optional[str]:
        return self.labels_by_id.get(entry_id)

    def get_branch(self, from_id: Optional[str] = None) -> List[SessionEntry]:
        """Walk from entry to root, returning all entries in path order."""
        path: List[SessionEntry] = []
        start_id = from_id or self.leaf_id
        current = self.by_id.get(start_id) if start_id else None
        while current:
            path.insert(0, current)
            current = self.by_id.get(current.parent_id) if current.parent_id else None
        return path

    def get_header(self) -> Optional[SessionHeader]:
        return next((e for e in self.file_entries if isinstance(e, SessionHeader)), None)

    def get_entries(self) -> List[SessionEntry]:
        """Get all session entries (excludes header). Returns a shallow copy."""
        return [e for e in self.file_entries if not isinstance(e, SessionHeader)]

    def get_session_id(self) -> str:
        return self.session_id

    def get_session_dir(self) -> str:
        return self.session_dir

    def get_session_file(self) -> Optional[str]:
        return self.session_file

    def get_cwd(self) -> str:
        return self.cwd

    def is_persisted(self) -> bool:
        return self.persist

    def build_session_context(self) -> SessionContext:
        """Build the message context to send to the LLM."""
        return build_session_context(self.get_entries(), self.leaf_id, self.by_id)

    def get_tree(self) -> List[SessionTreeNode]:
        """Get the session as a tree structure."""
        entries = self.get_entries()
        node_map: Dict[str, SessionTreeNode] = {}
        roots: List[SessionTreeNode] = []

        for entry in entries:
            node_map[entry.id] = SessionTreeNode(
                entry=entry,
                children=[],
                label=self.labels_by_id.get(entry.id),
                label_timestamp=self.label_timestamps_by_id.get(entry.id),
            )

        for entry in entries:
            node = node_map[entry.id]
            if entry.parent_id is None or entry.parent_id == entry.id:
                roots.append(node)
            else:
                parent = node_map.get(entry.parent_id)
                if parent:
                    parent.children.append(node)
                else:
                    roots.append(node)

        stack = list(roots)
        while stack:
            node = stack.pop()
            node.children.sort(key=lambda n: datetime.fromisoformat(n.entry.timestamp))
            stack.extend(node.children)

        return roots

    # =========================================================================
    # Branching
    # =========================================================================

    def branch(self, branch_from_id: str) -> None:
        """Move the leaf pointer to branch from an earlier entry."""
        if branch_from_id not in self.by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self.leaf_id = branch_from_id

    def reset_leaf(self) -> None:
        """Reset the leaf pointer to None (before any entries)."""
        self.leaf_id = None

    def branch_with_summary(
        self,
        branch_from_id: Optional[str],
        summary: str,
        details: Optional[Any] = None,
    ) -> str:
        """Branch from an entry and record a summary of the abandoned path."""
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

    def create_branched_session(self, leaf_id: str) -> Optional[str]:
        """Create a new session file containing only the path from root to the given leaf."""
        previous_session_file = self.session_file
        path = self.get_branch(leaf_id)
        if not path:
            raise ValueError(f"Entry {leaf_id} not found")

        path_without_labels = [e for e in path if not isinstance(e, LabelEntry)]
        path_ids = {e.id for e in path_without_labels}

        new_session_id = create_session_id()
        timestamp = datetime.now().isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")

        header = SessionHeader(
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            parent_id=None,
            cwd=self.cwd,
            parent_session_file=previous_session_file if self.persist else None,
        )

        # Collect labels for entries in the path
        labels_to_write = [
            {"target_id": tid, "label": lbl, "timestamp": self.label_timestamps_by_id.get(tid)}
            for tid, lbl in self.labels_by_id.items()
            if tid in path_ids
        ]

        existing_ids = set(path_ids)
        parent_id = path_without_labels[-1].id if path_without_labels else None
        label_entries: List[LabelEntry] = []
        for li in labels_to_write:
            le = LabelEntry(
                id=generate_id(existing_ids),
                parent_id=parent_id,
                timestamp=li["timestamp"],
                target_id=li["target_id"],
                label=li["label"],
            )
            existing_ids.add(le.id)
            label_entries.append(le)
            parent_id = le.id

        self.file_entries = [header, *path_without_labels, *label_entries]
        self.session_id = new_session_id
        self._build_index()

        if self.persist and self.session_dir:
            self.session_file = str(
                Path(self.session_dir) / f"{file_timestamp}_{new_session_id}.jsonl"
            )
            has_assistant = any(
                isinstance(e, LLMMessageEntry) and e.message.role == Role.ASSISTANT
                for e in self.file_entries
            )
            if has_assistant:
                self._rewrite_file()
                self.flushed = True
            else:
                self.flushed = False

            return self.session_file

        self.session_file = None
        return None

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @staticmethod
    def create(cwd: str, session_dir: Optional[str] = None) -> "SessionManager":
        """Create a new session."""
        dir_ = session_dir or get_default_session_dir(cwd)
        return SessionManager(cwd, dir_, None, True)

    @staticmethod
    def open(session_file: str, session_dir: Optional[str] = None, cwd_override: Optional[str] = None) -> "SessionManager":
        """Open a specific session file."""
        file_path = Path(session_file)
        entries = load_entries_from_file(str(file_path))
        header = next((e for e in entries if isinstance(e, SessionHeader)), None)
        cwd = cwd_override or (header.cwd if header else str(Path.cwd()))
        dir_ = session_dir or str(file_path.parent)
        return SessionManager(cwd, dir_, str(file_path), True)

    @staticmethod
    def continue_recent(cwd: str, session_dir: Optional[str] = None) -> "SessionManager":
        """Continue the most recent session, or create a new one if none exists."""
        session_dir = session_dir or get_default_session_dir(cwd)
        most_recent = find_most_recent_session_file(session_dir)
        if most_recent:
            return SessionManager(cwd, session_dir, most_recent, True)
        return SessionManager(cwd, session_dir, None, True)

    @staticmethod
    def in_memory(cwd: Optional[str] = None) -> "SessionManager":
        """Create an in-memory session (no file persistence)."""
        return SessionManager(cwd or str(Path.cwd()), "", None, False)

    @staticmethod
    def fork_from(source_file: str, target_cwd: str, session_dir: Optional[str] = None) -> "SessionManager":
        """Fork a session from another project directory into a new cwd."""
        source_file = Path(source_file)
        source_entries = load_entries_from_file(str(source_file))
        if not source_entries:
            raise ValueError(f"Cannot fork: source session is empty or invalid: {source_file}")

        header = next((e for e in source_entries if isinstance(e, SessionHeader)), None)
        if not header:
            raise ValueError(f"Cannot fork: source session has no header: {source_file}")

        dir_ = session_dir or get_default_session_dir(target_cwd)
        Path(dir_).mkdir(parents=True, exist_ok=True)

        new_session_id = create_session_id()
        timestamp = datetime.now().isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        new_file = str(Path(dir_) / f"{file_timestamp}_{new_session_id}.jsonl")

        new_header = SessionHeader(
            version=CURRENT_SESSION_VERSION,
            id=new_session_id,
            timestamp=timestamp,
            parent_id=None,
            cwd=target_cwd,
            parent_session_file=str(source_file),
        )

        with open(new_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(new_header)) + "\n")
            for entry in source_entries:
                if not isinstance(entry, SessionHeader):
                    f.write(json.dumps(asdict(entry)) + "\n")

        return SessionManager.open(new_file, dir_, target_cwd)

    @staticmethod
    def list(cwd: str, session_dir: Optional[str] = None, on_progress: Optional[SessionListProgress] = None) -> List[Dict[str, Any]]:
        """List all sessions for a cwd directory."""
        dir_ = session_dir or get_default_session_dir(cwd)
        sessions = list_sessions_from_dir(dir_, on_progress)
        sessions.sort(key=lambda s: s["modified"], reverse=True)
        return sessions

    @staticmethod
    def list_all(on_progress: Optional[SessionListProgress] = None) -> List[Dict[str, Any]]:
        """List all sessions across all project directories."""
        return list_all_sessions(on_progress)
