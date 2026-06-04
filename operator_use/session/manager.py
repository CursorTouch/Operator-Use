from __future__ import annotations
from typing import Any, Callable, List
from datetime import datetime
from pathlib import Path

from operator_use.session.types import (
    SessionFileEntry, SessionHeader,
    SessionEntry, LabelEntry, LeafEntry, CompactionEntry,
    SessionOptions, MessageEntry,
    ThinkingLevelChangeEntry, SessionInfoEntry,
    ModelChangeEntry, BranchEntry, CustomInfoEntry,
    CustomMessageEntry, SessionContext, SessionInfo,
    SessionTreeNode, ChannelEntry, MessageMeta, MessageAttachment,
)
from operator_use.session.utils import (
    create_session_id, generate_timestamp, generate_id, read_session_file,
    is_valid_session_file, find_most_recent_session, is_message_with_contents,
    get_last_activity_time, get_session_modified_date, build_session_info,
    list_sessions_from_dir, get_default_session_dir,
)
from operator_use.settings.paths import get_profiles_dir
from operator_use.message.types import (
    AgentMessage, AssistantMessage, CustomMessage, CompactionSummaryMessage,
    BranchSummaryMessage, ImageContent, TextContent, LLMMessage, Role,
)
from operator_use.inference.types import ThinkingLevel


class SessionManager:
    """Manages a single conversation session as a JSONL-backed linked list of entries."""

    def __init__(
        self,
        cwd: str | Path,
        session_dir: Path | None = None,
        session_file: Path | None = None,
        persist: bool = True,
    ):
        """Load an existing session file or start a new session, wiring up indexes and persistence."""
        self.session_id: str | None = None
        self.cwd = Path(cwd).resolve()
        self.persist = persist
        self.session_dir = (
            Path(session_dir).resolve() if session_dir
            else get_default_session_dir()
        )
        self.session_file = session_file
        self.by_id: dict[str, SessionEntry] = {}
        self.labels_by_id: dict[str, str] = {}
        self.label_timestamps_by_id: dict[str, float] = {}
        self.leaf_id: str | None = None
        self.entries: list[SessionFileEntry] = []
        self.flushed: bool = False

        if self.persist and not self.session_dir.exists():
            self.session_dir.mkdir(parents=True, exist_ok=True)

        if self.session_file:
            self.set_session(self.session_file)
        else:
            self.new_session()

    def set_session(self, session_file: Path):
        """Point the manager at an existing file, rebuilding indexes, or seed a fresh session if the file is absent/invalid."""
        self.session_file = session_file
        if session_file.exists():
            self.entries = read_session_file(session_file)

        if not self.entries:
            # File missing or invalid — start fresh in that file location
            session_id = create_session_id()
            header = SessionHeader(
                id=session_id,
                timestamp=generate_timestamp(),
                cwd=self.cwd,
            )
            self.session_id = session_id
            self.entries = [header]
            self._clear_indexes()
            self.flushed = False
            if self.persist:
                self._rewrite_file()
                self.flushed = True
        else:
            for entry in self.entries:
                if isinstance(entry, SessionHeader):
                    self.session_id = entry.id
                    break
            self._build_index()
            self.flushed = True

    def new_session(self, options: SessionOptions | None = None):
        """Reset state to a blank session, optionally using the supplied id or parent_session link."""
        options = options or SessionOptions()
        session_id = options.id or create_session_id()
        parent_session = Path(options.parent_session).resolve() if options.parent_session else None
        header = SessionHeader(
            id=session_id,
            timestamp=generate_timestamp(),
            cwd=self.cwd,
            parent_session=parent_session,
        )

        self.session_id = session_id
        self.entries = [header]
        self._clear_indexes()
        self.flushed = False

        if self.persist:
            file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            self.session_file = (self.session_dir / f"{file_timestamp}_{session_id}.jsonl").resolve()

        return self.session_file

    def _rewrite_file(self):
        """Atomically overwrite the session file with all current in-memory entries."""
        if not self.persist or not self.session_file:
            return None
        lines = [entry.model_dump_json(exclude_none=True) + "\n" for entry in self.entries]
        self.session_file.write_text("".join(lines), encoding="utf-8")

    def _clear_indexes(self) -> None:
        """Reset all in-memory indexes and set leaf_id to None."""
        self.by_id.clear()
        self.labels_by_id.clear()
        self.label_timestamps_by_id.clear()
        self.leaf_id = None

    def _build_index(self):
        """Rebuild by_id, labels, and leaf_id by scanning all current entries."""
        self._clear_indexes()

        for entry in self.entries:
            if isinstance(entry, SessionHeader):
                continue
            self.by_id[entry.id] = entry
            if isinstance(entry, LeafEntry):
                # LeafEntry records a navigation point — target_id is the new leaf.
                self.leaf_id = entry.target_id
            else:
                self.leaf_id = entry.id
            if isinstance(entry, LabelEntry):
                if entry.label:
                    self.labels_by_id[entry.target_id] = entry.label
                    self.label_timestamps_by_id[entry.target_id] = entry.timestamp
                else:
                    self.labels_by_id.pop(entry.target_id, None)
                    self.label_timestamps_by_id.pop(entry.target_id, None)

    def _persist(self, entry: SessionEntry):
        """Write entry to disk, but only after the first AssistantMessage has been recorded in this session."""
        if not self.persist or not self.session_file:
            return None

        has_assistant_message = any(
            isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
            for e in self.entries
        )

        if not has_assistant_message:
            self.flushed = False
            return

        if not self.flushed:
            self._rewrite_file()
            self.flushed = True
        else:
            with self.session_file.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json(exclude_none=True) + "\n")

    def _append_entry(self, entry: SessionEntry) -> str:
        """Append entry to in-memory list, update indexes, advance leaf_id, and persist if eligible."""
        self.entries.append(entry)
        self.by_id[entry.id] = entry
        self.leaf_id = entry.id
        self._persist(entry)
        return entry.id

    def append_message(self, message: AgentMessage, meta: MessageMeta | None = None) -> str:
        """Wrap `message` in a MessageEntry linked to the current leaf and append it.

        Args:
            message: The message to append (UserMessage, AssistantMessage, ToolMessage, etc).
            meta: Optional metadata (channel info, author, etc).

        Returns:
            The ID of the created MessageEntry.
        """
        entry = MessageEntry(message=message, parent_id=self.leaf_id, meta=meta)
        return self._append_entry(entry)

    def patch_entry_meta(self, entry_id: str, meta: "MessageMeta") -> bool:
        """Update the meta of an existing MessageEntry in-place and rewrite the session file.

        Args:
            entry_id: The ID of the MessageEntry to update.
            meta: The new metadata to assign.

        Returns:
            True if the entry existed and was updated, False otherwise.
        """
        entry = self.by_id.get(entry_id)
        if not isinstance(entry, MessageEntry):
            return False
        entry.meta = meta
        self._rewrite_file()
        return True

    def add_reaction(self, channel_message_id: str, emoji: str) -> bool:
        """Append an emoji reaction to the MessageEntry matching the given channel-side message ID.

        Args:
            channel_message_id: The external message ID from the gateway channel.
            emoji: The emoji string to append to the reactions list.

        Returns:
            True if a matching MessageEntry was found and updated, False otherwise.
        """
        for entry in self.entries:
            if not isinstance(entry, MessageEntry):
                continue
            if entry.meta and entry.meta.channel_message_id == channel_message_id:
                if entry.meta.reactions is None:
                    entry.meta.reactions = []
                if emoji not in entry.meta.reactions:
                    entry.meta.reactions.append(emoji)
                self._rewrite_file()
                return True
        return False

    def append_channel_entry(self, name: str, chat_id: str | None = None, user_id: str | None = None) -> str:
        """Record the active gateway channel at the current leaf so future context reconstruction knows the origin.

        Args:
            name: The channel name (e.g., 'slack', 'discord', 'telegram').
            chat_id: Optional chat/conversation ID from the channel.
            user_id: Optional user ID from the channel.

        Returns:
            The ID of the created ChannelEntry.
        """
        entry = ChannelEntry(name=name, chat_id=chat_id, user_id=user_id, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def get_current_channel(self) -> str | None:
        """Return the channel name most recently recorded on the active branch, or None.

        Returns:
            The channel name (e.g., 'slack', 'discord') or None if not set.
        """
        # Walk the active branch (not the flat log) so a branch we navigated
        # away from can't leak its channel into the current one.
        for entry in reversed(self.get_branch()):
            if isinstance(entry, ChannelEntry):
                return entry.name
        return None

    def append_thinking_level_change(self, thinking_level: ThinkingLevel) -> str:
        """Record a thinking-level change at the current leaf.

        Args:
            thinking_level: The new thinking level setting.

        Returns:
            The ID of the created ThinkingLevelChangeEntry.
        """
        entry = ThinkingLevelChangeEntry(thinking_level=thinking_level, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_model_change(self, model_id: str, provider_id: str) -> str:
        """Record a model/provider switch at the current leaf.

        Args:
            model_id: The new model identifier (e.g., 'claude-3-5-sonnet').
            provider_id: The new provider identifier (e.g., 'anthropic').

        Returns:
            The ID of the created ModelChangeEntry.
        """
        entry = ModelChangeEntry(model_id=model_id, provider_id=provider_id, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any | None = None,
        from_hook: bool = False,
    ) -> str:
        """Record a context-compaction event with its summary and the first entry that was retained.

        Args:
            summary: The compacted context summary or description.
            first_kept_entry_id: ID of the first message entry retained after compaction.
            tokens_before: Token count before compaction.
            details: Optional dict with compaction strategy details.
            from_hook: Whether compaction was triggered by a hook (vs. automatic).

        Returns:
            The ID of the created CompactionEntry.
        """
        entry = CompactionEntry(
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            details=details,
            from_hook=from_hook,
            parent_id=self.leaf_id,
        )
        return self._append_entry(entry)

    def append_label_change(self, target_id: str, label: str | None = None) -> str:
        """Attach or remove a human-readable label from the entry identified by `target_id`.

        Args:
            target_id: The entry ID to label or unlabel.
            label: The label string, or None to remove the label.

        Returns:
            The ID of the created LabelEntry.
        """
        entry = LabelEntry(target_id=target_id, label=label, parent_id=self.leaf_id)
        if label:
            self.labels_by_id[target_id] = label
            self.label_timestamps_by_id[target_id] = entry.timestamp
        else:
            self.labels_by_id.pop(target_id, None)
            self.label_timestamps_by_id.pop(target_id, None)
        return self._append_entry(entry)

    def append_custom_info(self, custom_type: str, data: Any | None = None) -> str:
        """Append an extension-defined structured metadata entry that carries no LLM-visible content.

        Args:
            custom_type: The custom type identifier (domain/extension-specific).
            data: Optional arbitrary structured data.

        Returns:
            The ID of the created CustomInfoEntry.
        """
        entry = CustomInfoEntry(custom_type=custom_type, data=data, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def append_custom_message(
        self,
        custom_type: str,
        content: Any,
        display: bool = True,
        details: Any | None = None,
    ) -> str:
        """Append an extension-defined displayable message that will be injected into the LLM context.

        Args:
            custom_type: The custom message type identifier.
            content: Message content (text, images, or arbitrary data).
            display: Whether to display this message in the UI (default True).
            details: Optional metadata or context dict.

        Returns:
            The ID of the created CustomMessageEntry.
        """
        entry = CustomMessageEntry(
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
            parent_id=self.leaf_id,
        )
        return self._append_entry(entry)

    def append_session_info(self, name: str | None = None) -> str:
        """Record a human-readable session name at the current leaf.

        Args:
            name: The session name or description.

        Returns:
            The ID of the created SessionInfoEntry.
        """
        entry = SessionInfoEntry(name=name, parent_id=self.leaf_id)
        return self._append_entry(entry)

    def get_session_name(self) -> str | None:
        """Return the most recent non-empty session name on the active branch, or None.

        Returns:
            The session name if set, or None.
        """
        # Branch-scoped, consistent with build_session_context / get_branch.
        for entry in reversed(self.get_branch()):
            if isinstance(entry, SessionInfoEntry) and entry.name and entry.name.strip():
                return entry.name.strip()
        return None

    def get_leaf_id(self) -> str | None:
        """Return the ID of the current leaf entry on the active branch.

        Returns:
            The current leaf ID, or None if the session has no entries.
        """
        return self.leaf_id

    def get_leaf_entry(self) -> SessionEntry | None:
        """Return the SessionEntry at the current leaf_id, or None if the session is empty.

        Returns:
            The current leaf SessionEntry, or None.
        """
        return self.by_id.get(self.leaf_id) if self.leaf_id else None

    def get_entry(self, id: str) -> SessionEntry | None:
        """Look up a single entry by its ID, returning None if not found.

        Args:
            id: The entry ID to look up.

        Returns:
            The SessionEntry, or None if not found.
        """
        return self.by_id.get(id)

    def get_children(self, parent_id: str) -> list[SessionEntry]:
        """Return all direct children of the given entry, sorted chronologically.

        Args:
            parent_id: The parent entry ID.

        Returns:
            List of child SessionEntries sorted by timestamp.
        """
        return sorted(
            [entry for entry in self.get_entries() if entry.parent_id == parent_id],
            key=lambda entry: entry.timestamp,
        )

    def get_label(self, id: str) -> str | None:
        """Return the human-readable label attached to the given entry id, or None.

        Args:
            id: The entry ID to get the label for.

        Returns:
            The label string, or None if not set.
        """
        return self.labels_by_id.get(id)

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        """Return entries from root to the given id (or leaf_id), in root→leaf order.

        Args:
            from_id: Optional entry ID to trace back to root from; defaults to current leaf_id.

        Returns:
            List of SessionEntries from root to the target, in order.
        """
        path: list[SessionEntry] = []
        seen: set[str] = set()
        cursor = from_id or self.leaf_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            current_entry = self.by_id.get(cursor)
            if not current_entry:
                break
            path.append(current_entry)
            cursor = current_entry.parent_id
        path.reverse()
        return path

    def build_session_context(self) -> SessionContext:
        """Reconstruct the LLM-ready message list and settings from the active branch, honouring any compaction boundary.

        Returns:
            A SessionContext with messages, thinking_level, model_id, and provider_id.
        """
        thinking_level: ThinkingLevel = ThinkingLevel.Off
        model_id: str | None = None
        provider_id: str | None = None
        messages: list[AgentMessage] = []
        compaction: CompactionEntry | None = None

        entries = self.get_branch()

        if not entries:
            return SessionContext(
                messages=messages,
                thinking_level=thinking_level,
                model_id=model_id,
                provider_id=provider_id,
            )

        for entry in entries:
            match entry:
                case ThinkingLevelChangeEntry():
                    thinking_level = entry.thinking_level
                case ModelChangeEntry():
                    model_id = entry.model_id
                    provider_id = entry.provider_id
                case CompactionEntry():
                    compaction = entry

        def append_message(entry: SessionEntry):
            match entry:
                case MessageEntry():
                    messages.append(entry.message)
                case CustomMessageEntry():
                    messages.append(CustomMessage.from_session(entry=entry))
                case BranchEntry():
                    messages.append(BranchSummaryMessage.from_session(entry=entry))

        if not compaction:
            for entry in entries:
                append_message(entry)
            return SessionContext(
                messages=messages,
                thinking_level=thinking_level,
                model_id=model_id,
                provider_id=provider_id,
            )

        messages.append(CompactionSummaryMessage.from_session(compaction))

        compaction_idx = entries.index(compaction)

        found_retained_from = False
        for entry in entries[:compaction_idx]:
            if entry.id == compaction.first_kept_entry_id:
                found_retained_from = True
            if found_retained_from:
                append_message(entry)

        for entry in entries[compaction_idx + 1:]:
            append_message(entry)

        return SessionContext(
            messages=messages,
            thinking_level=thinking_level,
            model_id=model_id,
            provider_id=provider_id,
        )

    def get_header(self) -> SessionHeader | None:
        """Return the SessionHeader for this session, or None if not yet written."""
        for entry in self.entries:
            if isinstance(entry, SessionHeader):
                return entry
        return None

    def get_entries(self) -> list[SessionEntry]:
        """Return all non-header entries in file order."""
        return [entry for entry in self.entries if not isinstance(entry, SessionHeader)]

    def get_tree(self) -> list[SessionTreeNode]:
        """Build a tree of SessionTreeNodes representing the full branching DAG of this session."""
        node_map: dict[str, SessionTreeNode] = {}
        roots: list[SessionTreeNode] = []

        for entry in self.get_entries():
            label = self.labels_by_id.get(entry.id)
            label_timestamp = self.label_timestamps_by_id.get(entry.id)
            node_map[entry.id] = SessionTreeNode(
                entry=entry,
                children=[],
                label_timestamp=label_timestamp,
                label=label,
            )

        for entry in self.get_entries():
            node = node_map[entry.id]
            if entry.parent_id is None or entry.parent_id == entry.id:
                roots.append(node)
            else:
                parent_node = node_map.get(entry.parent_id)
                if parent_node is None:
                    roots.append(node)
                else:
                    parent_node.children.append(node)

        stack = roots.copy()
        while stack:
            node = stack.pop()
            node.children.sort(key=lambda child: child.entry.timestamp)
            stack.extend(node.children)

        roots.sort(key=lambda node: node.entry.timestamp)
        return roots

    def branch(self, from_id: str):
        """Move the active leaf to `from_id`, persisting a LeafEntry so the navigation survives restarts."""
        if from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found.")
        # Persist a LeafEntry so the navigation point survives restarts.
        leaf_entry = LeafEntry(parent_id=self.leaf_id, target_id=from_id)
        self.entries.append(leaf_entry)
        self.by_id[leaf_entry.id] = leaf_entry
        self._persist(leaf_entry)
        self.leaf_id = from_id

    def reset_leaf(self):
        """Detach the current leaf pointer without writing a LeafEntry — used to seed a fresh turn."""
        self.leaf_id = None

    def branch_with_summary(
        self,
        summary: str,
        from_id: str | None = None,
        details: Any | None = None,
    ) -> str:
        """Create a BranchEntry at `from_id` carrying a prose summary of the diverged history."""
        if from_id is not None and from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found.")

        self.leaf_id = from_id

        entry = BranchEntry(
            parent_id=from_id,
            from_id=from_id or "root",
            summary=summary,
            details=details,
        )
        return self._append_entry(entry)

    def create_branched_session(self, leaf_id: str) -> Path | None:
        """Fork this session at `leaf_id` into a new file, rewriting only the entries on that branch."""
        previous_session_file = self.session_file
        path = self.get_branch(leaf_id)

        if not path:
            raise ValueError(f"Entry {leaf_id} not found.")

        path_without_labels = [entry for entry in path if not isinstance(entry, LabelEntry)]

        session_id = create_session_id()
        file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        new_session_file = self.session_dir / f"{file_timestamp}_{session_id}.jsonl"

        header = SessionHeader(
            id=session_id,
            timestamp=generate_timestamp(),
            cwd=self.cwd,
            parent_session=previous_session_file if self.persist else None,
        )

        path_entry_ids = {entry.id for entry in path_without_labels}
        labels_to_write: list[tuple[str, str, float]] = [
            (target_id, label, self.label_timestamps_by_id[target_id])
            for target_id, label in self.labels_by_id.items()
            if target_id in path_entry_ids
        ]

        label_entries: list[LabelEntry] = []
        last_entry = path_without_labels[-1] if path_without_labels else None
        parent_id = last_entry.id if last_entry else None
        used_ids = set(path_entry_ids)

        for (target_id, label, label_timestamp) in labels_to_write:
            label_entry = LabelEntry(
                id=generate_id(used_ids),
                parent_id=parent_id,
                timestamp=label_timestamp,
                target_id=target_id,
                label=label,
            )
            used_ids.add(label_entry.id)
            label_entries.append(label_entry)
            parent_id = label_entry.id

        self.entries = [header, *path_without_labels, *label_entries]
        self.session_id = session_id
        self.session_file = new_session_file if self.persist else None
        self._build_index()

        has_assistant = any(
            isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage)
            for entry in self.entries
        )

        if self.persist:
            if has_assistant:
                self._rewrite_file()
                self.flushed = True
            else:
                self.flushed = False
            return new_session_file
        return None

    @classmethod
    def create(cls, cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        """Convenience constructor: start a new session in `session_dir` (defaults to global sessions dir)."""
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir()
        return SessionManager(cwd, session_dir)

    @staticmethod
    def open(
        path: Path | str,
        session_dir: Path | str | None = None,
        cwd_override: Path | str | None = None,
    ) -> SessionManager:
        """Load an existing session JSONL file, optionally overriding the working directory."""
        path = Path(path).resolve()
        entries = read_session_file(path)
        header = next((e for e in entries if isinstance(e, SessionHeader)), None)
        if header is None:
            raise ValueError(f"No header found in session file: {path}")
        cwd = Path(cwd_override).resolve() if cwd_override else Path(header.cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else path.parent
        return SessionManager(cwd, session_dir, path)

    @staticmethod
    def continue_recent(cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        """Open the most recently modified session in `session_dir`, or create a new one if none exists."""
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir()
        most_recent = find_most_recent_session(session_dir)
        if most_recent:
            return SessionManager(cwd, session_dir, most_recent)
        return SessionManager(cwd, session_dir)

    @staticmethod
    def in_memory(cwd: Path | None = None) -> SessionManager:
        """Create a non-persisting session manager useful for tests and dry-run scenarios."""
        cwd = cwd or Path.cwd()
        return SessionManager(cwd, None, None, False)

    @staticmethod
    def fork_from(
        source: Path | str,
        target_cwd: Path | str,
        session_dir: Path | str | None = None,
    ) -> SessionManager:
        """Copy all entries from `source` into a new session file under `target_cwd`, linking it as a child session."""
        source = Path(source).resolve()
        target_cwd = Path(target_cwd).resolve()
        source_entries = read_session_file(source)

        if not source_entries:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source}")
        if not isinstance(source_entries[0], SessionHeader):
            raise ValueError(f"Cannot fork: source session has no header: {source}")

        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)

        new_session_id = create_session_id()
        file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        new_session_file = session_dir / f"{file_timestamp}_{new_session_id}.jsonl"

        new_header = SessionHeader(
            id=new_session_id,
            timestamp=generate_timestamp(),
            cwd=target_cwd,
            parent_session=source,
        )

        with new_session_file.open("w", encoding="utf-8") as f:
            f.write(new_header.model_dump_json() + "\n")
            for entry in source_entries:
                if isinstance(entry, SessionHeader):
                    continue
                f.write(entry.model_dump_json() + "\n")

        return SessionManager(target_cwd, session_dir, new_session_file)

    def list_own(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[SessionInfo]:
        """List all sessions in this manager's session_dir only.

        Safe for profile-scoped session managers — never leaks sessions from
        other profiles or the global sessions directory.
        """
        if self.session_dir is None or not self.session_dir.exists():
            return []
        sessions = list_sessions_from_dir(self.session_dir, on_progress=on_progress)
        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions

    @staticmethod
    def list(
        cwd: Path | str,
        session_dir: Path | str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[SessionInfo]:
        """List all sessions in `session_dir` (defaults to global sessions dir), sorted newest-first."""
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir()
        sessions = list_sessions_from_dir(session_dir, on_progress=on_progress)
        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions

    @staticmethod
    def list_all(on_progress: Callable[[int, int], None] | None = None) -> List[SessionInfo]:
        """List sessions across all profiles."""
        profiles_dir = get_profiles_dir()
        if not profiles_dir.exists():
            return []
        sessions: list[SessionInfo] = []
        try:
            for profile_dir in profiles_dir.iterdir():
                if not profile_dir.is_dir():
                    continue
                sessions_dir = profile_dir / 'sessions'
                if sessions_dir.is_dir():
                    sessions.extend(list_sessions_from_dir(sessions_dir, on_progress=on_progress))
        except Exception:
            pass
        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions
