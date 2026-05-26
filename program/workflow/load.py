from __future__ import annotations

from pathlib import Path

from program.workflow.execute import load_meta
from program.workflow.types import WorkflowMeta


class WorkflowLoader:
    """Discovers workflow Python files. Project-level files shadow global ones."""

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd

    def _dirs(self) -> list[Path]:
        from program.settings.paths import get_workflows_dir
        dirs = []
        if self._cwd:
            dirs.append(get_workflows_dir(self._cwd))
        dirs.append(get_workflows_dir())
        return dirs

    def find(self, name: str) -> Path | None:
        for d in self._dirs():
            path = d / f'{name}.py'
            if path.exists():
                return path
        return None

    def discover(self) -> list[Path]:
        seen: set[str] = set()
        paths: list[Path] = []
        for d in self._dirs():
            if not d.exists():
                continue
            for path in sorted(d.glob('*.py')):
                if path.stem not in seen:
                    seen.add(path.stem)
                    paths.append(path)
        return paths

    def list_with_meta(self) -> list[tuple[Path, WorkflowMeta]]:
        result = []
        for path in self.discover():
            try:
                meta = load_meta(path)
            except Exception:
                meta = WorkflowMeta(name=path.stem, description='')
            result.append((path, meta))
        return result
