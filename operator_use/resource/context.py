from __future__ import annotations

from pathlib import Path

from operator_use.resource.types import ContextFile

_CONTEXT_FILENAMES = ["AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"]


def _load_context_file_from_dir(directory: Path) -> ContextFile | None:
    """Load CLAUDE.md or AGENTS.md from directory, or None if not found."""
    for filename in _CONTEXT_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            try:
                return ContextFile(path=str(candidate), content=candidate.read_text(encoding='utf-8'))
            except OSError:
                pass
    return None


def load_project_context_files(cwd: Path, config_dir: Path) -> list[ContextFile]:
    """
    Load AGENTS.md / CLAUDE.md from the global config dir and every ancestor of cwd up to root.
    Returns files ordered: global first, then cwd ancestors root→cwd.
    """
    context_files: list[ContextFile] = []
    seen: set[str] = set()

    global_ctx = _load_context_file_from_dir(config_dir)
    if global_ctx:
        context_files.append(global_ctx)
        seen.add(global_ctx.path)

    ancestors: list[ContextFile] = []
    current = cwd.resolve()
    root = Path(current.root)

    while True:
        ctx = _load_context_file_from_dir(current)
        if ctx and ctx.path not in seen:
            ancestors.insert(0, ctx)
            seen.add(ctx.path)

        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    context_files.extend(ancestors)
    return context_files
