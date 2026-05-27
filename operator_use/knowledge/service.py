from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Knowledge:
    """
    Manages reference documents in ~/.operator/knowledge/ and .program/knowledge/.

    Two supported layouts — both work simultaneously:

    1. Directory nodes (preferred for multi-file topics):
         knowledge/products/index.md   → name "products"
         knowledge/api/v2/index.md     → name "api/v2"

    2. Flat files (simple single-topic docs):
         knowledge/company.md          → name "company"
         knowledge/pricing.md          → name "pricing"

    Project-level docs (.program/knowledge/) take precedence over global ones
    (~/.operator/knowledge/) when both define the same name.
    """

    def __init__(self, *knowledge_dirs: Path) -> None:
        """Accept one or more knowledge directories ordered by priority (highest first)."""
        self._dirs = [d for d in knowledge_dirs if d is not None]

    # ── File discovery ────────────────────────────────────────────────────────

    def list_files(self) -> list[dict]:
        """Return all knowledge entries, deduplicated by name (first dir wins)."""
        seen: set[str] = set()
        files: list[dict] = []

        for knowledge_dir in self._dirs:
            if not knowledge_dir.exists():
                continue
            self._collect(knowledge_dir, seen, files)

        return sorted(files, key=lambda f: f['name'])

    def _collect(self, knowledge_dir: Path, seen: set[str], files: list[dict]) -> None:
        # 1. Directory nodes: folders containing an index.md
        for path in sorted(knowledge_dir.rglob('index.md')):
            rel_dir = path.parent.relative_to(knowledge_dir)
            name = str(rel_dir).replace('\\', '/')
            if name == '.' or name in seen:
                continue
            seen.add(name)
            files.append({'name': name, 'path': path, 'preview': self._preview(path)})

        # 2. Flat .md files (not index.md)
        for path in sorted(knowledge_dir.rglob('*.md')):
            if path.name == 'index.md':
                continue
            rel = path.relative_to(knowledge_dir)
            name = str(rel.with_suffix('')).replace('\\', '/')
            if name in seen:
                continue
            seen.add(name)
            files.append({'name': name, 'path': path, 'preview': self._preview(path)})

    def _preview(self, path: Path) -> str:
        try:
            for line in path.read_text(encoding='utf-8').splitlines():
                stripped = line.strip().lstrip('#').strip()
                if stripped:
                    return stripped[:120]
        except Exception:
            pass
        return ''

    # ── System prompt injection ───────────────────────────────────────────────

    def build_knowledge_index(self) -> str | None:
        """Return a formatted markdown index for injection into the system prompt."""
        files = self.list_files()
        if not files:
            return None

        lines: list[str] = []
        current_group: str | None = None

        for f in files:
            parts = f['name'].split('/')
            group = parts[0] if len(parts) > 1 else None

            if group and group != current_group:
                lines.append(f'\n**{group}/**')
                current_group = group
            elif group is None:
                current_group = None

            indent = '  ' if group else ''
            entry = f"{indent}- **{f['name']}**"
            if f['preview']:
                entry += f" — {f['preview']}"
            entry += f" (`{f['path'].as_posix()}`)"
            lines.append(entry)

        logger.info('Knowledge index built | count=%d', len(files))
        return (
            '## Knowledge\n\n'
            'Reference documents available to you. Read the relevant file(s) when the task requires it.\n'
            + '\n'.join(lines)
        )
