from __future__ import annotations

from datetime import datetime, timezone

from operator_use.knowledge.prompts import consolidate_index, consolidate_overview, consolidate_run
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeConsolidateWorkflow(Workflow):
    name = 'knowledge-consolidate'
    description = 'Consolidate the knowledge base — deduplicate, merge near-identical pages, remove stale content.'
    when_to_use = 'Clean up and consolidate the knowledge base after multiple ingests.'
    phases = [
        {'name': 'read',        'description': 'Read all knowledge pages'},
        {'name': 'consolidate', 'description': 'Merge, deduplicate, clean'},
        {'name': 'index',       'description': 'Rebuild index.yaml and log.md'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        knowledge_dir = ctx.args.get('knowledge_dir', '')

        async with ctx.phase('read'):
            ctx.log('Reading knowledge pages...')
            overview = await ctx.agent(consolidate_overview(knowledge_dir), tools=['read'])

        async with ctx.phase('consolidate'):
            ctx.log('Consolidating knowledge base...')
            await ctx.agent(consolidate_run(knowledge_dir, overview), tools=['read', 'write'])

        async with ctx.phase('index'):
            ctx.log('Rebuilding index.yaml and updating log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(consolidate_index(knowledge_dir, timestamp), tools=['read', 'write'])

        return f"Knowledge consolidation complete at '{knowledge_dir}'."
