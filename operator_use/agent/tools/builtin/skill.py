"""Skill tool: load and invoke procedural skills from workspace."""

import yaml
from operator_use.tools.service import Tool, ToolResult
from operator_use.config.paths import get_named_workspace_dir
from operator_use.agent.skills.service import Skills, BUILTIN_SKILLS_DIR
from pydantic import BaseModel, Field


class SkillParams(BaseModel):
    name: str = Field(
        ...,
        description="Skill name to load. Skills can be in workspace/skills/{name}/SKILL.md or builtin skills directory. Examples: 'my-skill', 'debug-flow', 'google-workspace-cli'",
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
    """Load and present a skill from workspace or builtin skills."""
    workspace = kwargs.get("_workspace") or get_named_workspace_dir("operator")

    # Use Skills service to load from workspace or builtin
    skills_service = Skills(workspace)
    content = skills_service.load_skill_content(name)

    if content is None:
        workspace_skill = workspace / "skills" / name / "SKILL.md"
        builtin_skill = BUILTIN_SKILLS_DIR / name / "SKILL.md"
        return ToolResult.error_result(
            f"Skill not found: {name}\n"
            f"Expected path: {workspace_skill}\n"
            f"or builtin: {builtin_skill}\n"
            f"Create it at workspace/skills/{name}/SKILL.md"
        )

    # Determine which skill_dir was used (workspace takes precedence)
    workspace_skill_dir = workspace / "skills" / name
    builtin_skill_dir = BUILTIN_SKILLS_DIR / name

    if workspace_skill_dir.exists():
        skill_dir = workspace_skill_dir
    else:
        skill_dir = builtin_skill_dir

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
    scripts_dir = skill_dir / "scripts"
    references_dir = skill_dir / "references"
    assets_dir = skill_dir / "assets"

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
