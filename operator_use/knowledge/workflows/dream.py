from __future__ import annotations

from datetime import datetime, timezone

from operator_use.knowledge.prompts import dream_consolidate, dream_index, dream_read
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


class KnowledgeDreamWorkflow(Workflow):
    name = 'knowledge-dream'
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
            overview = await ctx.agent(dream_read(knowledge_dir), tools=['read'])

        async with ctx.phase('consolidate'):
            ctx.log('Consolidating knowledge base...')
            await ctx.agent(dream_consolidate(knowledge_dir, overview), tools=['read', 'write'])

        async with ctx.phase('index'):
            ctx.log('Rebuilding index.yaml and updating log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(dream_index(knowledge_dir, timestamp), tools=['read', 'write'])

        return f"Knowledge dream complete at '{knowledge_dir}'."
