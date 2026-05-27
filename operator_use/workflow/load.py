from __future__ import annotations

from pathlib import Path

from operator_use.workflow.execute import load_meta
from operator_use.workflow.types import WorkflowMeta


class WorkflowLoader:
    """Discovers workflow Python files from a profile's workflows dir."""

    def __init__(self, workflows_dir: Path) -> None:
        self._dir = workflows_dir

    def find(self, name: str) -> Path | None:
        path = self._dir / f'{name}.py'
        return path if path.exists() else None

    def discover(self) -> list[Path]:
        if not self._dir.exists():
            return []
        return sorted(self._dir.glob('*.py'))

    def list_with_meta(self) -> list[tuple[Path, WorkflowMeta]]:
        result = []
        for path in self.discover():
            try:
                meta = load_meta(path)
            except Exception:
                meta = WorkflowMeta(name=path.stem, description='')
            result.append((path, meta))
        return result
