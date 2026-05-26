from __future__ import annotations

from pydantic import BaseModel, Field

from program.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


class SkillViewSchema(BaseModel):
    name: str = Field(description='Skill name to load (must match an entry in available_skills).')


class SkillViewTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='skill_view',
            description=(
                'Load the full content of a skill by name. '
                'Use this when a skill in available_skills matches your current task. '
                'Returns the complete SKILL.md content including instructions and examples.'
            ),
            schema=SkillViewSchema,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Sequential,
        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        name = invocation.params.get('name', '').strip()
        if not name:
            return ToolResult.error(id=invocation.id, content="'name' is required.")

        loader = context.resource_loader if context else None
        if loader is None:
            return ToolResult.error(id=invocation.id, content='Resource loader unavailable.')

        skills, _ = loader.get_skills()
        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            available = ', '.join(s.name for s in skills) or 'none'
            return ToolResult.error(
                id=invocation.id,
                content=f"Skill '{name}' not found. Available: {available}",
            )

        try:
            content = skill.file_path.read_text(encoding='utf-8')
        except OSError as exc:
            return ToolResult.error(id=invocation.id, content=f'Cannot read skill file: {exc}')

        return ToolResult.ok(id=invocation.id, content=content)


tool = SkillViewTool()
