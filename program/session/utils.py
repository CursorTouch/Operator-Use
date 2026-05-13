from __future__ import annotations
from typing import Dict, Any, Set, Union, Optional, List, Callable, TYPE_CHECKING
from datetime import datetime
from pathlib import Path
import uuid
import json

if TYPE_CHECKING:
    from program.session.types import SessionEntry, FileEntry, SessionHeader
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


def infer_entry_type(entry: Dict[str, Any]) -> str:
    """Infer entry type from entry structure."""
    if "message" in entry:
        return "llm"
    if "thinking_level" in entry:
        return "thinking_level_change"
    if "model_id" in entry:
        return "model_change"
    if "first_kept_entry_id" in entry:
        return "compaction"
    if "from_id" in entry:
        return "branch_summary"
    if "target_id" in entry:
        return "label"
    if "name" in entry and "version" not in entry:
        return "session_info"
    if "custom_type" in entry and "content" in entry:
        return "custom_message"
    if "custom_type" in entry:
        return "custom"
    if "version" in entry:
        return "session_header"
    return "unknown"


def ensure_type_field(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure entry has type field for serialization."""
    if "type" not in entry:
        entry["type"] = infer_entry_type(entry)
    return entry


def deserialize_entry(data: Dict[str, Any]) -> Union[FileEntry, SessionEntry]:
    """Convert dict to proper dataclass instance."""
    from program.session.types import (
        SessionHeader, LLMMessageEntry, ThinkingLevelChangeEntry,
        ModelChangeEntry, CompactionEntry, BranchSummaryEntry,
        LabelEntry, SessionInfoEntry, CustomInfoEntry, CustomMessageEntry,
    )

    entry_type = data.get("type", "")

    try:
        if entry_type == "session_header":
            return SessionHeader(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                version=data.get("version", 1),
                cwd=data.get("cwd", ""),
                parent_session_path=data.get("parent_session_path")
            )
        elif entry_type == "llm":
            return LLMMessageEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                message=data.get("message")
            )
        elif entry_type == "thinking_level_change":
            return ThinkingLevelChangeEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                thinking_level=data.get("thinking_level")
            )
        elif entry_type == "model_change":
            return ModelChangeEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                provider=data.get("provider"),
                model_id=data.get("model_id")
            )
        elif entry_type == "compaction":
            return CompactionEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                summary=data.get("summary"),
                first_kept_entry_id=data.get("first_kept_entry_id"),
                tokens_before=data.get("tokens_before"),
                details=data.get("details")
            )
        elif entry_type == "branch_summary":
            return BranchSummaryEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                from_id=data.get("from_id"),
                summary=data.get("summary"),
                details=data.get("details")
            )
        elif entry_type == "label":
            return LabelEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                target_id=data.get("target_id"),
                label=data.get("label")
            )
        elif entry_type == "session_info":
            return SessionInfoEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                name=data.get("name")
            )
        elif entry_type == "custom_info":
            return CustomInfoEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                custom_type=data.get("custom_type"),
                data=data.get("data")
            )
        elif entry_type == "custom_message" or entry_type == "custom":
            return CustomMessageEntry(
                id=data.get("id"),
                parent_id=data.get("parent_id"),
                timestamp=data.get("timestamp"),
                custom_type=data.get("custom_type"),
                content=data.get("content"),
                display=data.get("display", False),
                details=data.get("details")
            )
        else:
            return data
    except Exception:
        return data


# =========================================================================
# Module-level Session Utilities
# =========================================================================

def parse_session_entries(content: str) -> List[FileEntry]:
    """Parse JSONL content into session entries."""
    entries: List[FileEntry] = []
    lines = content.strip().split("\n")

    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            entry = deserialize_entry(data)
            entries.append(entry)
        except (json.JSONDecodeError, Exception):
            continue

    return entries


def get_latest_compaction_entry(entries: List[SessionEntry]) -> Optional[Any]:
    """Get the most recent compaction entry from a list of entries."""
    from program.session.types import SessionEntryType, CompactionEntry

    for entry in reversed(entries):
        if entry.type == SessionEntryType.COMPACTION and isinstance(entry, CompactionEntry):
            return entry
    return None


def get_default_session_dir(cwd: str) -> str:
    """Compute the default session directory for a cwd."""
    safe_path = f"--{cwd.replace('/', '-').replace(chr(92), '-').replace(':', '-')}--"
    session_dir = Path.home() / ".claude" / "sessions" / safe_path
    session_dir.mkdir(parents=True, exist_ok=True)
    return str(session_dir)


def load_entries_from_file(file_path: str) -> List[FileEntry]:
    """Load entries from a JSONL session file."""
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
                entry = deserialize_entry(data)
                entries.append(entry)
            except (json.JSONDecodeError, Exception):
                continue
        return entries
    except Exception:
        return []


def is_valid_session_file(file_path: str) -> bool:
    """Check if a file is a valid session file."""
    try:
        path = Path(file_path)
        if not path.exists():
            return False

        content = path.read_text(encoding="utf-8", errors="ignore")
        first_line = content.split("\n")[0]
        if not first_line.strip():
            return False

        header = json.loads(first_line)
        return header.get("type") == "session_header" and isinstance(header.get("id"), str)
    except Exception:
        return False


def find_most_recent_session(session_dir: str) -> Optional[str]:
    """Find the most recently modified session file in a directory."""
    try:
        path = Path(session_dir)
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
    """Check if a message has text content."""
    return hasattr(message, "role") and hasattr(message, "contents") and len(message.contents) > 0


def extract_text_content(message: LLMMessage) -> str:
    """Extract text content from a message."""
    if not hasattr(message, "contents"):
        return ""

    from program.message.types import TextContent, ThinkingContent

    text_parts = []
    for content_block in message.contents:
        if isinstance(content_block, TextContent):
            text_parts.append(content_block.content)
        elif isinstance(content_block, ThinkingContent):
            text_parts.append(content_block.content)

    return " ".join(text_parts)


def get_last_activity_time(entries: List[FileEntry]) -> Optional[float]:
    """Get the timestamp of the last user/assistant message."""
    from program.session.types import SessionEntryType, LLMMessageEntry
    from program.message.types import Role

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

        if hasattr(message, "timestamp") and isinstance(message.timestamp, (int, float)):
            last_time = max(last_time or 0, message.timestamp)
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
    progress_total: Optional[int] = None
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
