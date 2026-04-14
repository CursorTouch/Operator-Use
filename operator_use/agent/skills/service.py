from pathlib import Path
import difflib
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


class Skills:
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self) -> list[str]:
        skills = []
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                skills.append(
                    {
                        "name": skill_dir.name,
                        "path": skill_dir,
                        "source": "workspace",
                    }
                )
        if self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                skills.append(
                    {
                        "name": skill_dir.name,
                        "path": skill_dir,
                        "source": "builtin",
                    }
                )
        return skills

    def load_skill_content(self, name: str) -> str | None:
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            logger.info(f"Skill loaded | name={name} source=workspace")
            return workspace_skill.read_text(encoding="utf-8")

        builtin_skill = self.builtin_skills / name / "SKILL.md"
        if builtin_skill.exists():
            logger.info(f"Skill loaded | name={name} source=builtin")
            return builtin_skill.read_text(encoding="utf-8")
        logger.warning(f"Skill not found | name={name}")
        return None

    def _strip_skill_formatter(self, skill_content: str) -> str:
        if skill_content.startswith("---"):
            pattern = r"^---\n.*?\n---\n"
            if match := re.match(pattern, skill_content, flags=re.DOTALL):
                return skill_content[match.end() :].strip()
        return skill_content

    def load_skill(self, name) -> str | None:
        if skill_content := self.load_skill_content(name):
            stripped_content = self._strip_skill_formatter(skill_content)
            return f"### Skill: {name}\n\n{stripped_content}"
        return None

    def load_skills_for_context(self, names: list[str]) -> dict[str, str]:
        parts = []
        for name in names:
            if part := self.load_skill(name):
                parts.append(part)
        return "\n\n---\n\n".join(parts)

    def get_skill_metadata(self, name: str) -> dict:
        if skill_content := self.load_skill_content(name):
            # Match content between --- and ---
            pattern = r"^---\n(.*?)\n---"
            match = re.search(pattern, skill_content, flags=re.DOTALL)

            if match:
                metadata_block = match.group(1).strip()
                metadata_dict = {}

                for line in metadata_block.splitlines():
                    line = line.strip()
                    if not line or ":" not in line:
                        continue

                    key, value = line.split(":", 1)  # split only first colon
                    metadata_dict[key.strip()] = value.strip()

                return metadata_dict
        return {}

    def snapshot(self, skill_path: Path) -> None:
        """Save a timestamped snapshot + diff of skill content before it is overwritten."""
        if not skill_path.exists():
            return
        current = skill_path.read_text(encoding="utf-8")
        history_dir = skill_path.parent / ".history"
        history_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        # Diff against the most recent snapshot (before saving the new one)
        existing = sorted(history_dir.glob("*.md"))
        if existing:
            prev_content = existing[-1].read_text(encoding="utf-8")
            diff_lines = list(
                difflib.unified_diff(
                    prev_content.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    fromfile=existing[-1].name,
                    tofile=f"{timestamp}.md",
                )
            )
            if diff_lines:
                (history_dir / f"{timestamp}.diff").write_text(
                    "".join(diff_lines), encoding="utf-8"
                )

        # Full snapshot
        (history_dir / f"{timestamp}.md").write_text(current, encoding="utf-8")
        logger.info(f"Skill snapshot saved | skill={skill_path.parent.name} timestamp={timestamp}")

    def register_history_hook(self, hooks) -> None:
        """Register a BEFORE_TOOL_CALL hook that snapshots skills before write_file/edit_file."""
        from operator_use.agent.hooks.events import HookEvent

        workspace = self.workspace

        async def _skill_history(ctx) -> None:
            if ctx.tool_call.name not in ("write_file", "edit_file"):
                return
            path_param = ctx.tool_call.params.get("path", "")
            if not path_param:
                return
            p = Path(path_param)
            if not p.is_absolute():
                p = workspace / p
            # Match pattern: .../skills/{name}/SKILL.md
            if p.name == "SKILL.md" and p.parent.parent.name == "skills":
                self.snapshot(p)

        hooks.register(HookEvent.BEFORE_TOOL_CALL, _skill_history)

    def build_skills_summary(self) -> str:
        lines = []
        skills = self.list_skills()
        logger.info(f"Available skills | {[s['name'] + '(' + s['source'] + ')' for s in skills]}")
        for skill in skills:
            name = skill["name"]
            metadata = self.get_skill_metadata(name)
            path = skill["path"].as_posix()
            lines.append(f"### {name}")
            lines.append(f" - Description: {metadata.get('description', '')}")
            lines.append(f" - Path: {path}")
        return "\n".join(lines)
