from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class Knowledge:
    """Manages reference documents in workspace/knowledge/.

    Two supported conventions — both work simultaneously:

    1. Directory nodes (preferred):
       knowledge/products/pricing/index.md   → name: "products/pricing"
       knowledge/support/index.md            → name: "support"

    2. Flat files (legacy / simple):
       knowledge/company.md                    → name: "company"
    """

    def __init__(self, workspace: Path) -> None:
        self.knowledge_dir = workspace / "knowledge"

    def list_files(self) -> list[dict]:
        if not self.knowledge_dir.exists():
            return []

        seen: set[str] = set()
        files = []

        # 1. Directory nodes: any context.md found anywhere in the tree
        for path in sorted(self.knowledge_dir.rglob("index.md")):
            rel_dir = path.parent.relative_to(self.knowledge_dir)
            name = str(rel_dir).replace("\\", "/")
            if name in seen:
                continue
            seen.add(name)
            files.append({"name": name, "path": path, "preview": self._preview(path)})

        # 2. Flat .md files that are not named index.md
        for path in sorted(self.knowledge_dir.rglob("*.md")):
            if path.name == "index.md":
                continue
            rel = path.relative_to(self.knowledge_dir)
            name = str(rel.with_suffix("")).replace("\\", "/")
            if name in seen:
                continue
            seen.add(name)
            files.append({"name": name, "path": path, "preview": self._preview(path)})

        return sorted(files, key=lambda f: f["name"])

    def _preview(self, path: Path) -> str:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    return stripped
        except Exception:
            pass
        return ""

    def build_knowledge_index(self) -> str | None:
        files = self.list_files()
        if not files:
            return None

        lines = []
        current_group: str | None = None

        for f in files:
            parts = f["name"].split("/")
            group = parts[0] if len(parts) > 1 else None

            if group and group != current_group:
                lines.append(f"\n**{group}/**")
                current_group = group
            elif group is None:
                current_group = None

            indent = "  " if group else ""
            entry = f"{indent}- **{f['name']}**"
            if f["preview"]:
                entry += f" — {f['preview']}"
            entry += f" (`{f['path'].as_posix()}`)"
            lines.append(entry)

        logger.info(f"Knowledge files indexed | count={len(files)}")
        return (
            "## Knowledge\n\n"
            "Reference documents in your workspace. Read them when relevant to the task.\n"
            + "\n".join(lines)
        )
