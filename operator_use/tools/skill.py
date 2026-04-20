"""Skill tool: load and invoke procedural skills from profile."""

import yaml
from operator_use.agent.tools.service import Tool, ToolResult
from operator_use.config.paths import get_named_profile_dir
from operator_use.agent.skills.service import Skills, BUILTIN_SKILLS_DIR
from pydantic import BaseModel, Field


class SkillParams(BaseModel):
    name: str = Field(
        ...,
        description="Skill name to load. Skills can be in profile/skills/{name}/SKILL.md or builtin skills directory. Examples: 'my-skill', 'debug-flow', 'google-workspace-cli'",
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
    description="Load and invoke a procedural skill from profile. Skills are Markdown files with YAML metadata at profile/skills/{name}/SKILL.md. They can contain instructions, scripts (in skills/{name}/scripts/), or references (in skills/{name}/references/). Returns the skill's full content and metadata so you can follow it step-by-step.",
    model=SkillParams,
)
async def skill(
    name: str,
    args: str | None = None,
    **kwargs,
) -> ToolResult:
    """Load and present a skill from profile or builtin skills."""
    profile_root = kwargs.get("_profile") or kwargs.get("_workspace") or get_named_profile_dir("operator")

    # Use Skills service to load from profile or builtin
    skills_service = Skills(profile_root)
    content = skills_service.invoke_skill(name)

    if content is None:
        profile_skill = profile_root / "skills" / name / "SKILL.md"
        builtin_skill = BUILTIN_SKILLS_DIR / name / "SKILL.md"
        return ToolResult.error_result(
            f"Skill not found: {name}\n"
            f"Expected path: {profile_skill}\n"
            f"or builtin: {builtin_skill}\n"
            f"Create it at profile/skills/{name}/SKILL.md"
        )

    # Determine which skill_dir was used (profile takes precedence)
    profile_skill_dir = profile_root / "skills" / name
    builtin_skill_dir = BUILTIN_SKILLS_DIR / name

    if profile_skill_dir.exists():
        skill_dir = profile_skill_dir
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
