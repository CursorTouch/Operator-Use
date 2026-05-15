from __future__ import annotations

from program.skill.types import Skill


def format_skills_for_prompt(skills: list[Skill]) -> str:
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""

    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory "
        "(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in visible:
        lines += [
            "  <skill>",
            f"    <name>{_escape_xml(skill.name)}</name>",
            f"    <description>{_escape_xml(skill.description)}</description>",
            f"    <location>{_escape_xml(skill.file_path)}</location>",
            "  </skill>",
        ]
    lines.append("</available_skills>")
    return "\n".join(lines)


def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
