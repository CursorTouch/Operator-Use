"""RestartInterceptor — git-free snapshot/revert for self-improvement cycles.

When the agent edits Python files and restarts, RestartInterceptor
(a BEFORE_TOOL_CALL hook) snapshots each file's original content before it is
overwritten.  If the new worker fails to start, the supervisor calls
revert_session() to restore every file from those snapshots without needing git.

InterceptorLog keeps a persistent JSONL record of consecutive attempts,
grouped by run_id, so the agent receives the full failure history on retry.
"""

import difflib
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from operator_use.interceptor.base import Interceptor

logger = logging.getLogger(__name__)


class RestartInterceptor(Interceptor):
    """Snapshots .py files before write_file / edit_file so they can be
    restored without git if a self-improvement restart breaks startup.

    A *session* groups all file changes made during one self-improvement cycle.
    The session ID is written into restart.json so the supervisor knows
    which snapshots to restore on startup failure.

    Mirrors the Skills history-hook pattern so the integration with the
    agent's hook system is identical.
    """

    def __init__(self, userdata: Path, project_root: Path) -> None:
        self.userdata = userdata
        self.project_root = project_root
        self._session_id: Optional[str] = None
        self._session_dir: Optional[Path] = None
        self._manifest: list[dict] = []

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = datetime.now().strftime("%Y%m%dT%H%M%S")
            self._session_dir = self.userdata / "code_snapshots" / self._session_id
            self._session_dir.mkdir(parents=True, exist_ok=True)
            self._manifest = []
            logger.info("RestartInterceptor: session started (%s)", self._session_id)
        return self._session_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def reset(self) -> None:
        self._session_id = None
        self._session_dir = None
        self._manifest = []

    # ------------------------------------------------------------------
    # Snapshot (called by hook before each write)
    # ------------------------------------------------------------------

    def snapshot(self, file_path: Path) -> None:
        """Save original content before a file is overwritten.

        Only tracks .py files inside project_root.  If the same file is
        edited twice in one session the first snapshot is kept, so revert
        always goes to the pre-session state.
        """
        if not file_path.exists():
            return
        try:
            file_path.relative_to(self.project_root)
        except ValueError:
            return  # outside project — ignore

        self._ensure_session()

        if any(e["path"] == str(file_path) for e in self._manifest):
            return  # first snapshot wins

        original = file_path.read_text(encoding="utf-8")
        file_hash = hashlib.md5(str(file_path).encode()).hexdigest()[:10]  # nosec B324 — used for filename only, not security
        snapshot_file = self._session_dir / f"{file_hash}.original"
        snapshot_file.write_text(original, encoding="utf-8")

        self._manifest.append({"path": str(file_path), "snapshot": str(snapshot_file)})
        self._save_manifest()
        logger.debug("RestartInterceptor: snapshot saved for %s", file_path.name)

    def _save_manifest(self) -> None:
        (self._session_dir / "manifest.json").write_text(
            json.dumps(self._manifest, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Diff generation (persisted for InterceptorLog + LLM synthesis)
    # ------------------------------------------------------------------

    def generate_diffs(self) -> list[dict]:
        """Compute unified diffs: snapshot → current file state.

        Writes .diff files to the session directory and returns
        {"path": str, "diff": str} for each file that changed.
        """
        diffs = []
        for entry in self._manifest:
            path = Path(entry["path"])
            snapshot_file = Path(entry["snapshot"])
            if not path.exists() or not snapshot_file.exists():
                continue
            original = snapshot_file.read_text(encoding="utf-8")
            current = path.read_text(encoding="utf-8")
            diff_lines = list(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    fromfile=f"{path.name} (before)",
                    tofile=f"{path.name} (after)",
                    lineterm="",
                )
            )
            if diff_lines:
                diff_text = "\n".join(diff_lines)
                diff_file = snapshot_file.with_suffix(".diff")
                diff_file.write_text(diff_text, encoding="utf-8")
                diffs.append({"path": str(path), "diff": diff_text})
        return diffs

    # ------------------------------------------------------------------
    # Hook registration (mirrors Skills.register_history_hook)
    # ------------------------------------------------------------------

    def register_history_hook(self, hooks) -> None:
        """Register a BEFORE_TOOL_CALL hook that snapshots .py files
        before write_file / edit_file executes."""
        from operator_use.agent.hooks.events import HookEvent

        interceptor = self

        async def _restart_interceptor_hook(ctx) -> None:
            if ctx.tool_call.name not in ("write_file", "edit_file"):
                return
            path_param = ctx.tool_call.params.get("path", "")
            if not path_param:
                return
            p = Path(path_param)
            if not p.is_absolute():
                p = interceptor.project_root / p
            if p.suffix != ".py":
                return
            interceptor.snapshot(p)

        hooks.register(HookEvent.BEFORE_TOOL_CALL, _restart_interceptor_hook)


# ---------------------------------------------------------------------------
# Standalone recovery helpers — called by the supervisor (no agent available)
# ---------------------------------------------------------------------------


def revert_session(session_id: str, userdata: Path) -> list[str]:
    """Restore all files in a session to their pre-session originals.

    Returns the list of file paths that were restored.
    """
    session_dir = userdata / "code_snapshots" / session_id
    manifest_file = session_dir / "manifest.json"
    if not manifest_file.exists():
        logger.warning("revert_session: manifest not found for session %s", session_id)
        return []

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    reverted: list[str] = []
    for entry in manifest:
        path = Path(entry["path"])
        snapshot_file = Path(entry["snapshot"])
        if snapshot_file.exists():
            try:
                path.write_text(snapshot_file.read_text(encoding="utf-8"), encoding="utf-8")
                reverted.append(str(path))
                logger.info("revert_session: restored %s", path.name)
            except Exception as exc:
                logger.error("revert_session: failed to restore %s: %s", path, exc)
    return reverted


def load_session_diffs(session_id: str, userdata: Path) -> list[dict]:
    """Load pre-computed .diff files for a session.

    Returns list of {"file": stem, "diff": text}.
    """
    session_dir = userdata / "code_snapshots" / session_id
    if not session_dir.exists():
        return []
    return [
        {"file": f.stem, "diff": f.read_text(encoding="utf-8")}
        for f in sorted(session_dir.glob("*.diff"))
    ]


# ---------------------------------------------------------------------------
# InterceptorLog
# ---------------------------------------------------------------------------


class InterceptorLog:
    """Persistent JSONL record of self-improvement attempts.

    All attempts are appended to a single ``interceptor_log.jsonl`` that
    is never deleted or overwritten.  Each entry carries a ``run_id`` that
    groups the consecutive failures belonging to one improvement cycle.

    A *run* is the full sequence of attempts for a single task, from the
    first failure through to either success or abandonment.  When a new
    task starts after a successful restart the supervisor generates a fresh
    ``run_id``, so ``get_for_run()`` only returns entries relevant to the
    current cycle — not stale history from a completely different task.

    Example layout on disk::

        interceptor_log.jsonl
          {"run_id": "R-20260327T143000", "attempt": 1, ...}  ← run A, fail 1
          {"run_id": "R-20260327T143000", "attempt": 2, ...}  ← run A, fail 2
          {"run_id": "R-20260327T143000", "attempt": 5, ...}  ← run A, fail 5
          (run A succeeds on 6th try — no entry written for success)
          {"run_id": "R-20260327T151200", "attempt": 1, ...}  ← run B, fail 1
    """

    def __init__(self, userdata: Path) -> None:
        self.log_file = userdata / "interceptor_log.jsonl"

    def append(
        self,
        *,
        run_id: str,
        task_preview: str,
        session_id: Optional[str],
        files_changed: list[str],
        error_preview: str,
        reverted_files: list[str],
    ) -> None:
        attempt = len(self.get_for_run(run_id)) + 1
        entry = {
            "run_id": run_id,
            "attempt": attempt,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "task_preview": task_preview[:200],
            "files_changed": files_changed,
            "error_preview": error_preview[-600:],
            "reverted_files": reverted_files,
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.error("InterceptorLog: failed to append: %s", exc)

    def get_for_run(self, run_id: str) -> list[dict]:
        """Return all entries for one run_id, in attempt order."""
        return [e for e in self._read_all() if e.get("run_id") == run_id]

    def get_recent(self, n: int = 5) -> list[dict]:
        """Return the last *n* entries across all runs (for debugging)."""
        return self._read_all()[-n:]

    def _read_all(self) -> list[dict]:
        if not self.log_file.exists():
            return []
        entries: list[dict] = []
        try:
            with open(self.log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        return entries
