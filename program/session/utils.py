from typing import Dict, Any, Set
import uuid


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


def ensure_type_field(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure entry has type field for serialization."""
    if "type" not in entry:
        entry["type"] = infer_entry_type(entry)
    return entry
