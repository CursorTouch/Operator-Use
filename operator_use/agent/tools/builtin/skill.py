"""Skill tool: load and invoke procedural skills from workspace."""

import yaml
from operator_use.tools.service import Tool, ToolResult
from operator_use.paths import get_named_workspace_dir
from pydantic import BaseModel, Field


class SkillParams(BaseModel):
    name: str = Field(
        ...,
        description="Skill name to load. Skills are in workspace/skills/{name}/SKILL.md. Examples: 'my-skill', 'debug-flow', 'test-runner'",
    )
    args: str | None = Field(
        default=None,
        description="Optional arguments to pass to the skill (free-form string, parsed by the skill itself)",
    )


def _parse_skill_metadata(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from skill markdown.

    Returns: (metadata dict, skill body)
    """
    if not content.startswith("---"):
        return {}, content

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        yaml_str = parts[1]
        body = parts[2].strip()
        metadata = yaml.safe_load(yaml_str) or {}
        return metadata, body
    except Exception:
        return {}, content


@Tool(
    name="skill",
    description="Load and invoke a procedural skill from workspace. Skills are Markdown files with YAML metadata at workspace/skills/{name}/SKILL.md. They can contain instructions, scripts (in skills/{name}/scripts/), or references (in skills/{name}/references/). Returns the skill's full content and metadata so you can follow it step-by-step.",
    model=SkillParams,
)
async def skill(
    name: str,
    args: str | None = None,
    **kwargs,
) -> ToolResult:
    """Load and present a skill from workspace."""
    workspace = kwargs.get("_workspace") or get_named_workspace_dir("operator")
    skill_file = workspace / "skills" / name / "SKILL.md"

    if not skill_file.exists():
        return ToolResult.error_result(
            f"Skill not found: {name}\n"
            f"Expected path: {skill_file}\n"
            f"Create it at workspace/skills/{name}/SKILL.md"
        )

    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult.error_result(f"Failed to read skill '{name}': {e}")

    # Parse YAML frontmatter
    metadata, body = _parse_skill_metadata(content)

    # Build response
    response_parts = []

    if metadata:
        response_parts.append("## Skill Metadata")
        for key, val in metadata.items():
            response_parts.append(f"- **{key}**: {val}")
        response_parts.append("")

    if args:
        response_parts.append("## Arguments")
        response_parts.append(f"{args}")
        response_parts.append("")

    response_parts.append("## Skill Content")
    response_parts.append(body)

    # Note about referenced files
    scripts_dir = workspace / "skills" / name / "scripts"
    references_dir = workspace / "skills" / name / "references"
    assets_dir = workspace / "skills" / name / "assets"

    if any(d.exists() for d in [scripts_dir, references_dir, assets_dir]):
        response_parts.append("")
        response_parts.append("## Associated Resources")
        if scripts_dir.exists():
            scripts = list(scripts_dir.glob("*"))
            if scripts:
                response_parts.append(f"- **Scripts**: {', '.join(s.name for s in scripts)}")
        if references_dir.exists():
            refs = list(references_dir.glob("*"))
            if refs:
                response_parts.append(f"- **References**: {', '.join(r.name for r in refs)}")
        if assets_dir.exists():
            assets = list(assets_dir.glob("*"))
            if assets:
                response_parts.append(f"- **Assets**: {', '.join(a.name for a in assets)}")

    return ToolResult.success_result("\n".join(response_parts))
