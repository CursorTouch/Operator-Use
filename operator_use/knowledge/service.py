from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INDEX_FILENAME = 'index.yaml'


class Knowledge:
    """
    Manages reference documents in the profile knowledge/ directory.

    Two modes — tried in order per directory:

    1. index.yaml (preferred) — explicit manifest with always_load, tags, priority:
         - always_load: true  → content injected directly into the system prompt
         - always_load: false → listed as available for on-demand read

    2. Filesystem scan (fallback when no index.yaml) — discovers index.md nodes
       and flat .md files; all treated as on-demand.
    """

    def __init__(self, *knowledge_dirs: Path) -> None:
        """Accept one or more knowledge directories ordered by priority (highest first)."""
        self._dirs = [d for d in knowledge_dirs if d is not None]

    # ── Index loading ─────────────────────────────────────────────────────────

    def _load_index(self, knowledge_dir: Path) -> list[dict[str, Any]] | None:
        """Load index.yaml from a knowledge directory. Returns None if absent/invalid."""
        index_path = knowledge_dir / INDEX_FILENAME
        if not index_path.exists():
            return None
        try:
            import yaml
        except ImportError:
            logger.warning('PyYAML not installed — cannot load %s; pip install pyyaml', index_path)
            return None
        try:
            data = yaml.safe_load(index_path.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return data
            logger.warning('%s must be a YAML list — ignoring', index_path)
        except Exception as e:
            logger.warning('Failed to load %s: %s', index_path, e)
        return None

    # ── Filesystem scan (fallback) ────────────────────────────────────────────

    def list_files(self) -> list[dict]:
        """Return all knowledge entries discovered from filesystem, deduplicated by name."""
        seen: set[str] = set()
        files: list[dict] = []
        for knowledge_dir in self._dirs:
            if not knowledge_dir.exists():
                continue
            self._collect(knowledge_dir, seen, files)
        return sorted(files, key=lambda f: f['name'])

    def _collect(self, knowledge_dir: Path, seen: set[str], files: list[dict]) -> None:
        for path in sorted(knowledge_dir.rglob('index.md')):
            rel_dir = path.parent.relative_to(knowledge_dir)
            name = str(rel_dir).replace('\\', '/')
            if name == '.' or name in seen:
                continue
            seen.add(name)
            files.append({'name': name, 'path': path, 'preview': self._preview(path)})

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

    def build_knowledge_for_prompt(self) -> str | None:
        """
        Build knowledge section for system prompt injection.

        With index.yaml:
          - always_load entries → <knowledge> blocks with full content
          - remaining entries   → <available_knowledge> list

        Without index.yaml (filesystem scan):
          - all discovered files → <available_knowledge> list
        """
        always_blocks: list[str] = []
        available: list[dict[str, str]] = []

        for knowledge_dir in self._dirs:
            if not knowledge_dir.exists():
                continue

            index = self._load_index(knowledge_dir)

            if index is not None:
                for entry in index:
                    rel_path = entry.get('path', '')
                    if not rel_path:
                        continue
                    full_path = knowledge_dir / rel_path
                    always = entry.get('always_load', False)
                    tags = ','.join(entry.get('tags', []))
                    priority = entry.get('priority', 'normal')

                    if always:
                        try:
                            content = full_path.read_text(encoding='utf-8')
                            always_blocks.append(
                                f'<knowledge path="{rel_path}">\n{content.strip()}\n</knowledge>'
                            )
                        except Exception as e:
                            logger.warning('Failed to load always_load knowledge %s: %s', full_path, e)
                    else:
                        attrs: dict[str, str] = {'path': rel_path, 'priority': priority}
                        if tags:
                            attrs['tags'] = tags
                        available.append(attrs)
            else:
                # Filesystem scan fallback — all on-demand
                seen: set[str] = set()
                files: list[dict] = []
                self._collect(knowledge_dir, seen, files)
                for f in files:
                    rel = f['path'].relative_to(knowledge_dir).as_posix()
                    entry_attrs: dict[str, str] = {'path': rel, 'priority': 'normal'}
                    if f.get('preview'):
                        entry_attrs['description'] = f['preview']
                    available.append(entry_attrs)

        if not always_blocks and not available:
            return None

        sections: list[str] = ['## Knowledge\n']

        if always_blocks:
            sections.extend(always_blocks)

        if available:
            lines = ['<available_knowledge>']
            for doc in available:
                attr_str = ' '.join(f'{k}="{v}"' for k, v in doc.items())
                lines.append(f'  <doc {attr_str} />')
            lines.append('</available_knowledge>')
            lines.append('\nUse the `read` tool to load any available knowledge document when needed.')
            sections.append('\n'.join(lines))

        logger.info('Knowledge prompt built | always_load=%d available=%d',
                    len(always_blocks), len(available))
        return '\n\n'.join(sections)

    # Keep old name as alias
    def build_knowledge_index(self) -> str | None:
        return self.build_knowledge_for_prompt()
