from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel


class LCMSettings(BaseModel):
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000
    condense_threshold: int = 4   # D0 nodes needed before condensing to D1
    max_depth: int = 3            # maximum DAG depth
    db_path: Path | None = None   # defaults to ~/.operator/lcm.db


@dataclass
class StoredMessage:
    store_id: int
    session_id: str
    role: str
    content_text: str   # plain-text extraction for FTS
    content_json: str   # full JSON for reconstruction
    timestamp: float


@dataclass
class LCMNode:
    session_id: str
    depth: int
    summary: str
    source_ids: list[int] = field(default_factory=list)
    source_type: str = "messages"   # "messages" | "nodes"
    expand_hint: str = ""
    created_at: float = 0.0
    node_id: int = 0                # set after insert
