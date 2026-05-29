from __future__ import annotations

from operator_use.knowledge.prompts import lint_report, lint_scan
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
            overview = await ctx.agent(lint_scan(knowledge_dir), tools=['read'])

        async with ctx.phase('report'):
            ctx.log('Checking for issues...')
            report = await ctx.agent(lint_report(knowledge_dir, overview), tools=['read'])

        return report
