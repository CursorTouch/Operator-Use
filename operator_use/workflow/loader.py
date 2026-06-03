from __future__ import annotations

from pathlib import Path

from operator_use.workflow.execute import load_meta
from operator_use.workflow.types import WorkflowMeta


class WorkflowLoader:
    """Discovers workflow Python files from one or more directories.

    Dirs are searched in order; the first match for a given name wins.
    """

    def __init__(self, *dirs: Path) -> None:
        self._dirs = [d for d in dirs if d is not None]

    def find(self, name: str) -> Path | None:
        """Search for a workflow file by name; return the first match or None."""
        for d in self._dirs:
            path = d / f'{name}.py'
            if path.exists():
                return path
        return None

    def discover(self) -> list[Path]:
        """Find all workflow .py files across configured dirs; deduplicate by filename."""
        seen: set[str] = set()
        result: list[Path] = []
        for d in self._dirs:
            if not d.exists():
                continue
            for path in sorted(d.glob('*.py')):
                # Skip __init__.py and already-seen names (first-found wins).
                if path.name == '__init__.py' or path.name in seen:
                    continue
                seen.add(path.name)
                result.append(path)
        return result

    def list_with_meta(self) -> list[tuple[Path, WorkflowMeta]]:
        """Discover all workflows and extract their metadata; fallback to stem on parse error."""
        result = []
        for path in self.discover():
            try:
                meta = load_meta(path)
            except Exception:
                # If metadata parse fails, use filename as name and empty description.
                meta = WorkflowMeta(name=path.stem, description='')
            result.append((path, meta))
        return result
