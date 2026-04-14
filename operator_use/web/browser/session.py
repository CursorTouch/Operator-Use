from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TargetSession:
    target_id: str
    session_id: str
    url: str = ""
    title: str = ""


class Session:
    """Owns page target/session bookkeeping for a Browser instance."""

    def __init__(self) -> None:
        self._targets: dict[str, dict] = {}
        self._sessions: dict[str, str] = {}
        self._current_target_id: str | None = None

    @property
    def targets(self) -> dict[str, dict]:
        return self._targets

    @property
    def sessions(self) -> dict[str, str]:
        return self._sessions

    @property
    def current_target_id(self) -> str | None:
        return self._current_target_id

    @current_target_id.setter
    def current_target_id(self, value: str | None) -> None:
        self._current_target_id = value

    def clear(self) -> None:
        self._targets.clear()
        self._sessions.clear()
        self._current_target_id = None

    def register_target(
        self, target_id: str, session_id: str, url: str = "", title: str = ""
    ) -> None:
        self._targets[target_id] = {"url": url, "title": title}
        self._sessions[target_id] = session_id
        if self._current_target_id is None:
            self._current_target_id = target_id

    def update_target(
        self, target_id: str, *, url: str | None = None, title: str | None = None
    ) -> None:
        if target_id not in self._targets:
            return
        if url is not None:
            self._targets[target_id]["url"] = url
        if title is not None:
            self._targets[target_id]["title"] = title

    def remove_by_target(self, target_id: str) -> str | None:
        session_id = self._sessions.pop(target_id, None)
        self._targets.pop(target_id, None)
        if self._current_target_id == target_id:
            self._current_target_id = next(iter(self._sessions), None)
        return session_id

    def find_target_by_session(self, session_id: str) -> str | None:
        return next(
            (target_id for target_id, sid in self._sessions.items() if sid == session_id), None
        )

    def current_session_id(self) -> str | None:
        if self._current_target_id is None:
            return None
        return self._sessions.get(self._current_target_id)

    def remaining_targets(self, excluding_target_id: str) -> list[str]:
        return [target_id for target_id in self._sessions if target_id != excluding_target_id]
