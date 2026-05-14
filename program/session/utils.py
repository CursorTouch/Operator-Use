from __future__ import annotations
from typing import Dict, Any, Set, Optional, List, Callable, TYPE_CHECKING
from datetime import datetime
from pathlib import Path
import uuid
import json

if TYPE_CHECKING:
    from program.session.types import (
        SessionEntry, FileEntry, SessionHeader, LLMMessageEntry,
        ThinkingLevelChangeEntry, ModelChangeEntry, CompactionSummaryEntry,
        BranchSummaryEntry, LabelEntry, SessionInfoEntry, CustomInfoEntry,
        CustomMessageEntry, SessionContext
    )
    from program.message.types import LLMMessage

# Type alias for progress callback
SessionListProgress = Callable[[int, int], None]


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


# =========================================================================
# Module-level Session Utilities
# =========================================================================

def get_latest_compaction_entry(entries: List[SessionEntry]) -> Optional[Any]:
    """Get the most recent compaction entry from a list of entries."""
    from program.session.types import SessionEntryType, CompactionSummaryEntry

    for entry in reversed(entries):
        if entry.type == SessionEntryType.COMPACTION_SUMMARY and isinstance(entry, CompactionSummaryEntry):
            return entry
    return None


def get_default_session_dir(cwd: str) -> str:
    """Compute the default session directory for a cwd."""
    from program.settings.paths import get_config_dir
    safe_path = f"--{cwd.lstrip('/').lstrip(chr(92)).replace('/', '-').replace(chr(92), '-').replace(':', '-')}--"
    session_dir = get_config_dir(None) / "sessions" / safe_path
    session_dir.mkdir(parents=True, exist_ok=True)
    return str(session_dir)


def load_entries_from_file(file_path: str) -> List[FileEntry]:
    """Load entries from a JSONL session file."""
    from program.session.types import SessionEntryType
    
    path = Path(file_path)
    if not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8")
        entries: List[FileEntry] = []

        for line in content.strip().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                entry_type = data.get("type", "").lower()
                
                match entry_type:
                    case SessionEntryType.SESSION_HEADER:
                        entry = SessionHeader(**data)
                    case SessionEntryType.LLM:
                        entry = LLMMessageEntry(**data)
                    case SessionEntryType.THINKING_LEVEL_CHANGE:
                        entry = ThinkingLevelChangeEntry(**data)
                    case SessionEntryType.MODEL_CHANGE:
                        entry = ModelChangeEntry(**data)
                    case SessionEntryType.COMPACTION_SUMMARY:
                        entry = CompactionSummaryEntry(**data)
                    case SessionEntryType.BRANCH_SUMMARY:
                        entry = BranchSummaryEntry(**data)
                    case SessionEntryType.LABEL:
                        entry = LabelEntry(**data)
                    case SessionEntryType.SESSION_INFO:
                        entry = SessionInfoEntry(**data)
                    case SessionEntryType.CUSTOM_INFO:
                        entry = CustomInfoEntry(**data)
                    case SessionEntryType.CUSTOM_MESSAGE:
                        entry = CustomMessageEntry(**data)
                    case _:
                        continue

                entries.append(entry)
            except (json.JSONDecodeError, Exception):
                continue

        return entries
    except Exception:
        return []


def is_valid_session_file(session_file_path: str) -> bool:
    """Check if a file is a valid session file."""
    from program.session.types import SessionEntryType
    
    try:
        path = Path(session_file_path)
        if not path.exists() or not path.is_file():
            return False

        content = path.read_text(encoding="utf-8", errors="ignore")
        first_line = content.split("\n")[0]
        if not first_line.strip():
            return False

        header = json.loads(first_line)
        return header.get("type") == SessionEntryType.SESSION_HEADER and isinstance(header.get("id"), str)
    except Exception:
        return False


def find_most_recent_session_path(session_path: str) -> Optional[str]:
    """Find the most recently modified session file in a directory."""
    try:
        path = Path(session_path)
        if not path.exists():
            return None

        files = [
            f for f in path.glob("*.jsonl")
            if is_valid_session_file(str(f))
        ]

        if not files:
            return None

        most_recent = max(files, key=lambda p: p.stat().st_mtime)
        return str(most_recent)
    except Exception:
        return None


def is_message_with_content(message: LLMMessage) -> bool:
    """Check if a message has content."""
    return len(message.contents) > 0

def extract_text_content(message: LLMMessage) -> str:
    """Extract text content from a message."""

    from program.message.types import TextContent, ThinkingContent

    text_parts = []
    for content in message.contents:
        match content:
            case TextContent(content=content):
                text_parts.append(content)
            case ThinkingContent(content=content):
                text_parts.append(content)

    return " ".join(text_parts)

def get_last_activity_time(entries: List[FileEntry]) -> Optional[float]:
    """Get the timestamp of the last user/assistant message."""
    from program.session.types import SessionEntryType, LLMMessageEntry
    from program.message.types import Role, AssistantMessage

    last_time: Optional[float] = None

    for entry in entries:
        if entry.type != SessionEntryType.LLM:
            continue

        if not isinstance(entry, LLMMessageEntry):
            continue

        message = entry.message
        if not is_message_with_content(message):
            continue

        if message.role not in (Role.USER, Role.ASSISTANT):
            continue

        if isinstance(message, AssistantMessage) and isinstance(entry.timestamp, (int, float)):
            last_time = max(last_time or 0, entry.timestamp)
            continue

        try:
            entry_time = datetime.fromisoformat(entry.timestamp).timestamp()
            last_time = max(last_time or 0, entry_time)
        except Exception:
            continue

    return last_time


def get_session_modified_date(
    entries: List[FileEntry],
    header: SessionHeader,
    file_mtime: datetime
) -> datetime:
    """Get the actual modified date of a session."""
    last_activity = get_last_activity_time(entries)
    if last_activity:
        return datetime.fromtimestamp(last_activity)

    try:
        header_time = datetime.fromisoformat(header.timestamp)
        return header_time
    except Exception:
        return file_mtime


def build_session_info(file_path: str) -> Optional[Dict[str, Any]]:
    """Build session info from a session file."""
    from program.session.types import SessionHeader, SessionInfoEntry, LLMMessageEntry

    try:
        path = Path(file_path)
        if not path.exists():
            return None

        entries = load_entries_from_file(file_path)
        if not entries:
            return None

        header = next(
            (e for e in entries if isinstance(e, SessionHeader)),
            None
        )
        if not header:
            return None

        stats = path.stat()
        message_count = 0
        session_name: Optional[str] = None

        for entry in entries:
            if isinstance(entry, SessionInfoEntry):
                session_name = entry.name.strip() if entry.name else None

            if not isinstance(entry, LLMMessageEntry):
                continue

            message_count += 1

        modified = get_session_modified_date(entries, header, datetime.fromtimestamp(stats.st_mtime))

        return {
            "path": str(file_path),
            "id": header.id,
            "cwd": header.cwd or "",
            "name": session_name,
            "created": datetime.fromisoformat(header.timestamp),
            "modified": modified,
            "messageCount": message_count,
        }
    except Exception:
        return None


def list_sessions_from_dir(
    session_dir: str,
    on_progress: Optional[SessionListProgress] = None,
    progress_offset: int = 0,
    progress_total: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """List all sessions in a directory."""
    sessions: List[Dict[str, Any]] = []
    path = Path(session_dir)

    if not path.exists():
        return sessions

    try:
        files = list(path.glob("*.jsonl"))
        total = progress_total or len(files)
        loaded = 0

        for file_path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            info = build_session_info(str(file_path))
            loaded += 1
            if on_progress:
                on_progress(progress_offset + loaded, total)

            if info:
                sessions.append(info)
    except Exception:
        pass

    return sessions


def list_all_sessions(
    on_progress: Optional[SessionListProgress] = None,
) -> List[Dict[str, Any]]:
    """List all sessions across all project directories."""
    from program.settings.paths import get_config_dir

    sessions_root = get_config_dir(None) / "sessions"
    if not sessions_root.exists():
        return []

    try:
        subdirs = [d for d in sessions_root.iterdir() if d.is_dir()]
    except Exception:
        return []

    all_files: List[Path] = []
    for subdir in subdirs:
        try:
            all_files.extend(subdir.glob("*.jsonl"))
        except Exception:
            pass

    total = len(all_files)
    loaded = 0
    sessions: List[Dict[str, Any]] = []

    for file_path in all_files:
        info = build_session_info(str(file_path))
        loaded += 1
        if on_progress:
            on_progress(loaded, total)
        if info:
            sessions.append(info)

    sessions.sort(key=lambda s: s["modified"], reverse=True)
    return sessions


def build_session_context(
    entries: List[SessionEntry],
    leaf_id: Optional[str] = None,
    by_id: Optional[Dict[str, Any]] = None,
) -> "SessionContext":
    """Build LLM message context from session entries via tree traversal."""
    from program.session.types import (
        SessionContext, SessionEntryType, LLMMessageEntry,
        CompactionSummaryEntry, BranchSummaryEntry, CustomMessageEntry,
    )
    from program.message.types import UserMessage, TextContent, Role
    from program.llm.types import ThinkingLevel

    if by_id is None:
        by_id = {e.id: e for e in entries}

    leaf = by_id.get(leaf_id) if leaf_id else (entries[-1] if entries else None)
    if not leaf:
        return SessionContext(messages=[], thinking_level=None)

    # Walk from leaf to root
    entries: List[SessionEntry] = []
    current: Optional[SessionEntry] = leaf
    while current:
        entries.insert(0, current)
        current = by_id.get(current.parent_id) if current.parent_id else None

    # Collect last thinking_level, model, and compaction along the path
    thinking_level: Optional[ThinkingLevel] = None
    model: Optional[Dict[str, str]] = None
    compaction: Optional[CompactionSummaryEntry] = None

    for entry in entries:
        match entry:
            case ThinkingLevelChangeEntry(thinking_level=thinking_level):
                thinking_level = thinking_level
            case ModelChangeEntry(provider=provider, model_id=model_id):
                model = {"provider": provider, "model_id": model_id}
            case CompactionSummaryEntry() as compaction:
                compaction = compaction

    messages: List[LLMMessage] = []

    def _user_msg(text: str) -> UserMessage:
        msg = UserMessage()
        msg.contents = [TextContent(content=text)]
        return msg

    def _emit(entry: SessionEntry) -> None:
        match entry:
            case LLMMessageEntry(message=message):
                messages.append(message)
            case CustomMessageEntry(content=content):
                if isinstance(content, str):
                    messages.append(_user_msg(content))
                else:
                    msg = UserMessage()
                    msg.contents = list(content)
                    messages.append(msg)
            case BranchSummaryEntry(from_id=from_id, summary=summary):
                messages.append(_user_msg(f"[Branch summary from {from_id}]\n\n{summary}"))

    if compaction:
        messages.append(_user_msg(
            f"[Compacted conversation. {compaction.tokens_before} tokens before.]\n\n{compaction.summary}"
        ))
        comp_idx = next(
            (i for i, e in enumerate(entries) if isinstance(e, CompactionSummaryEntry) and e.id == compaction.id),
            -1,
        )
        found_first_kept = False
        for i in range(comp_idx):
            e = entries[i]
            if e.id == compaction.first_kept_entry_id:
                found_first_kept = True
            if found_first_kept:
                _emit(e)
        for i in range(comp_idx + 1, len(entries)):
            _emit(entries[i])
    else:
        for entry in entries:
            _emit(entry)

    return SessionContext(messages=messages, thinking_level=thinking_level, model=model)
