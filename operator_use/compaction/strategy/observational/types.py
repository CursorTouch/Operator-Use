from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

OM_OBSERVATIONS_RECORDED = "om.observations.recorded"
OM_REFLECTIONS_RECORDED = "om.reflections.recorded"
OM_OBSERVATIONS_DROPPED = "om.observations.dropped"


def _make_id(content: str, timestamp: str) -> str:
    seed = f"{content[:64]}{timestamp}".encode()
    return hashlib.sha256(seed).hexdigest()[:12]


def _token_count(content: str) -> int:
    return max(1, len(content) // 4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


@dataclass
class Observation:
    id: str
    content: str
    timestamp: str
    relevance: Literal["low", "medium", "high", "critical"]
    source_entry_ids: list[str]
    token_count: int

    @classmethod
    def create(
        cls,
        content: str,
        relevance: Literal["low", "medium", "high", "critical"],
        source_entry_ids: list[str],
        timestamp: str | None = None,
    ) -> Observation:
        ts = timestamp or _now_iso()
        return cls(
            id=_make_id(content, ts),
            content=content,
            timestamp=ts,
            relevance=relevance,
            source_entry_ids=source_entry_ids,
            token_count=_token_count(content),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "timestamp": self.timestamp,
            "relevance": self.relevance,
            "source_entry_ids": self.source_entry_ids,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Observation:
        return cls(
            id=str(d["id"]),
            content=str(d["content"]),
            timestamp=str(d.get("timestamp", "")),
            relevance=d.get("relevance", "medium"),
            source_entry_ids=list(d.get("source_entry_ids") or []),
            token_count=int(d.get("token_count") or _token_count(d["content"])),
        )


@dataclass
class Reflection:
    id: str
    content: str
    supporting_observation_ids: list[str]
    token_count: int

    @classmethod
    def create(cls, content: str, supporting_observation_ids: list[str]) -> Reflection:
        ts = _now_iso()
        return cls(
            id=_make_id(content, ts),
            content=content,
            supporting_observation_ids=supporting_observation_ids,
            token_count=_token_count(content),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "supporting_observation_ids": self.supporting_observation_ids,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Reflection:
        return cls(
            id=str(d["id"]),
            content=str(d["content"]),
            supporting_observation_ids=list(d.get("supporting_observation_ids") or []),
            token_count=int(d.get("token_count") or _token_count(d["content"])),
        )


@dataclass
class LedgerFold:
    active_observations: list[Observation] = field(default_factory=list)
    reflections: list[Reflection] = field(default_factory=list)
    latest_observation_coverage_id: str | None = None
    latest_reflection_coverage_id: str | None = None
