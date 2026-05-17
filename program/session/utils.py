import uuid
from typing import Any, Callable
from datetime import datetime
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
import re

from program.session.types import (
    SessionEntry, SessionHeader, SessionInfo, MessageEntry, SessionFileEntry, SessionType
)
from program.message.types import AgentMessage, LLMMessage, Role, TextContent, ImageContent
from program.settings.paths import get_agent_dir

def create_session_id() -> str:
    """Create a new session ID using UUIDv7."""
    return str(uuid.uuid7())

def generate_id(by_id: Any) -> str:
    """
    Generate a unique short ID (8 hex chars, collision-checked).
    
    Args:
        by_id: A container (like a set or dict) that supports the 'in' operator 
               to check for existing IDs.
    """
    for _ in range(100):
        new_id = str(uuid.uuid4())[:8]
        if new_id not in by_id:
            return new_id

    # Fallback to full UUID if somehow we have collisions
    return str(uuid.uuid4())

def generate_timestamp() -> float:
    now = datetime.now()
    return now.timestamp()

def get_default_session_dir(cwd: str | Path, agent_dir: Path | None = None) -> Path:
    """
    Get the default session directory for a cwd.
    Encodes cwd into a safe directory name under ~/.program/agent/sessions/.
    Creates the directory if it doesn't exist. Pass agent_dir to override the
    base directory (used for test isolation).
    """
    cwd = Path(cwd).as_posix()
    safe_path = f"--{re.sub(r'^[/\\]', '', cwd).replace('/', '-').replace('\\', '-').replace(':', '-')}--"
    base = agent_dir if agent_dir is not None else get_agent_dir()
    session_dir = base / "sessions" / safe_path
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

def read_session_file(session_file: Path) -> list[SessionFileEntry]:
    if not session_file.exists():
        return []

    adapter = TypeAdapter(SessionFileEntry)

    content = session_file.read_text(encoding="utf-8")
    entries: list[SessionFileEntry] = []

    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = adapter.validate_json(line)
            entries.append(entry)
        except Exception:
            continue

    if len(entries) == 0:
        return []

    header = entries[0]

    if header.type != SessionType.SESSION_HEADER:
        return []

    return entries

def is_valid_session_file(session_file: Path | str) -> bool:
    try:
        path = Path(session_file)
        if not path.exists():
            return False

        with path.open("r", encoding="utf-8") as file:
            first_line = file.readline().strip()
            
        if not first_line:
            return False
        
        SessionHeader.model_validate_json(first_line)
        return True
    except (OSError, ValidationError, ValueError):
        return False

def find_most_recent_session(session_dir: Path | str) -> Path | None:
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return None

    candidate_sessions = [p for p in session_dir.glob("*.jsonl") if is_valid_session_file(p)]

    if not candidate_sessions:
        return None

    most_recent = max(candidate_sessions, key=lambda x: x.stat().st_mtime)
    return most_recent

def is_message_with_contents(message: AgentMessage) -> bool:
    if not isinstance(message, LLMMessage):
        return False
    if message.role not in (Role.USER, Role.ASSISTANT):
        return False
    return any(isinstance(c, (TextContent, ImageContent)) for c in message.contents)

def get_last_activity_time(entries: list[SessionEntry]) -> float | None:
    last_activity_time = None
    
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue

        if not is_message_with_contents(entry.message):
            continue
        
        message_timestamp = getattr(entry.message, "timestamp", None)
        if message_timestamp is None:
            timestamp = entry.timestamp
        elif isinstance(message_timestamp, (int, float)):
            timestamp = float(message_timestamp)
        else:
            timestamp = float(message_timestamp.timestamp())

        last_activity_time = max(last_activity_time or 0.0, timestamp)
    
    return last_activity_time

def get_session_modified_date(entries: list[SessionEntry], header: SessionHeader | None = None) -> datetime:
    if last_activity_time := get_last_activity_time(entries=entries):
        return datetime.fromtimestamp(last_activity_time)

    header = header or entries[0]
    return datetime.fromtimestamp(header.timestamp)

def build_session_info(file: Path) -> SessionInfo | None:
    content = file.read_text(encoding="utf-8")

    file_entries: list[SessionFileEntry] = []
    lines = content.strip().splitlines()
    adapter = TypeAdapter(SessionFileEntry)

    for line in lines:
        if not line.strip():
            continue
        try:
            file_entries.append(adapter.validate_json(line))
        except Exception:
            pass

    if len(file_entries) == 0:
        return None

    header: SessionHeader | None = None
    entries: list[SessionEntry] = []
    message_count = 0
    for entry in file_entries:
        if isinstance(entry, SessionHeader):
            header = entry
        else:
            entries.append(entry)
            if isinstance(entry, MessageEntry):
                message_count += 1

    if header is None:
        return None
    
    cwd = header.cwd
    parent_session = header.parent_session
    created = datetime.fromtimestamp(header.timestamp)
    modified = get_session_modified_date(entries, header)

    return SessionInfo(
        path=file,
        id=header.id,
        cwd=cwd,
        parent_session=parent_session,
        created=created,
        modified=modified,
        message_count=message_count
    )

def list_sessions_from_dir(
    dir_path: Path | str,
    on_progress: Callable[[int, int], None] | None = None,
    progress_offset: int = 0,
    progress_total: int | None = None
) -> list[SessionInfo]:
    """
    Read all .jsonl session files in a directory and return a list of SessionInfo objects.
    Optionally reports progress through the on_progress callback.
    """
    sessions: list[SessionInfo] = []
    dir_path = Path(dir_path)
    
    if not dir_path.exists() or not dir_path.is_dir():
        return sessions

    try:
        files = list(dir_path.glob("*.jsonl"))
        total = progress_total if progress_total is not None else len(files)
        loaded = 0

        # We process files sequentially since Python I/O blocking is usually fine here,
        # but could be updated to use ThreadPoolExecutor if concurrency is strictly needed.
        for file in files:
            info = build_session_info(file)
            loaded += 1
            if on_progress:
                on_progress(progress_offset + loaded, total)
            
            if info is not None:
                sessions.append(info)
                
    except Exception:
        pass  # Return what we have on error, or an empty list if early

    return sessions