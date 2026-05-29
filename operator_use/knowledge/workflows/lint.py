from __future__ import annotations

from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeLintWorkflow(Workflow):
    name = 'knowledge-lint'
    description = 'Check knowledge pages for contradictions, stale claims, and broken cross-links.'
    when_to_use = 'Audit the knowledge base for quality issues.'
    phases = [
        {'name': 'scan',   'description': 'Read all knowledge pages'},
        {'name': 'report', 'description': 'Identify and report issues'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        async with ctx.phase('scan'):
            ctx.log('Reading all knowledge pages...')
            overview = await ctx.agent(
                f"List every .md file in '{knowledge_dir}' and return a one-sentence "
                "summary of each page's topic.",
                tools=['read'],
            )

        async with ctx.phase('report'):
            ctx.log('Checking for issues...')
            report = await ctx.agent(
                f"Audit the knowledge base at '{knowledge_dir}'.\n\n"
                f"Pages overview:\n{overview}\n\n"
                "Read all pages, then produce a structured report with these sections:\n"
                "1. **Contradictions** — claims on different pages that conflict.\n"
                "2. **Stale content** — claims that appear outdated or superseded.\n"
                "3. **Broken cross-links** — references to pages or anchors that don't exist.\n"
                "4. **Near-duplicate pages** — pages with substantial content overlap.\n\n"
                "For each finding include: page(s) affected, the issue, and a suggested fix. "
                "If no issues in a category, say 'None found.'",
                tools=['read'],
            )

        return report
