from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from operator_use.knowledge.service import Knowledge
from operator_use.knowledge.workflows.consolidate import KnowledgeConsolidateWorkflow
from operator_use.knowledge.workflows.ingest import KnowledgeIngestWorkflow
from operator_use.knowledge.workflows.lint import KnowledgeLintWorkflow
from operator_use.knowledge.workflows.query import KnowledgeQueryWorkflow
from operator_use.tool.types import Tool, ToolInvocation, ToolKind, ToolResult
from operator_use.workflow.types import WorkflowContext, WorkflowInvocation

if TYPE_CHECKING:
    from operator_use.tool.types import AbortSignal, ToolContext, ToolExecutionUpdateCallback


class KnowledgeAction(StrEnum):
    list   = 'list'
    query  = 'query'
    add    = 'add'
    ingest = 'ingest'
    lint   = 'lint'
    consolidate = 'consolidate'
    log    = 'log'


class KnowledgeSchema(BaseModel):
    action: KnowledgeAction = Field(
        description=(
            'Action to perform:\n'
            '  list    — list all pages with a one-line preview\n'
            '  query   — answer a question using the knowledge base (requires: query)\n'
            '  add     — write content directly into a knowledge page without any intermediate file (requires: page, content); use this when you already have the content\n'
            '  ingest  — synthesize a source into knowledge pages (requires: source — accepts a URL, a file path, or raw text)\n'
            '  lint    — check for contradictions and stale content\n'
            '  consolidate — consolidate and deduplicate all pages\n'
            '  log     — return the audit log of past operations'
        )
    )
    query: str = Field(default='', description='Question or keyword(s) for query.')
    page: str = Field(default='', description='Target page name (no extension) for add.')
    content: str = Field(default='', description='Text to append for add.')
    source: str = Field(default='', description=(
        'Source for ingest — one of:\n'
        '  YouTube URL (youtube.com/... or youtu.be/...) — extract transcript and synthesize\n'
        '  URL  (https://...)  — web page to fetch and synthesize\n'
        '  file (/path/to/file) — existing file on disk\n'
        '  text (any other string) — raw text content to synthesize directly'
    ))


KnowledgeSchema.model_rebuild()


def _get_knowledge_dir(context: ToolContext | None) -> Path | None:
    if context is None:
        return None
    loader = context.resource_loader
    if loader is None:
        return None
    profile = getattr(loader, '_active_profile', None)
    return profile.knowledge_dir if profile else None


def _source_type(source: str) -> Literal['youtube', 'url', 'file', 'text']:
    if 'youtube.com/' in source or 'youtu.be/' in source:
        return 'youtube'
    if source.startswith(('http://', 'https://')):
        return 'url'
    if source.startswith(('/', './', '../', '~')) or Path(source).exists():
        return 'file'
    return 'text'




def _make_workflow_context(context: ToolContext) -> WorkflowContext:
    tools = context.engine.tools if context.engine is not None else []
    return WorkflowContext(llm=context.llm, tools=tools)


class KnowledgeTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='knowledge',
            description='Manage the knowledge base stored in the active profile\'s knowledge/ directory.',
            schema=KnowledgeSchema,
            kind=ToolKind.Unknown,
        )

    def is_available(self, context) -> bool:
        return _get_knowledge_dir(context) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = KnowledgeSchema.model_validate(invocation.params)
        knowledge_dir = _get_knowledge_dir(context)

        if knowledge_dir is None:
            return ToolResult.error(invocation.id, "No knowledge directory — start with --agent <profile>.")

        match params.action:

            case KnowledgeAction.list:
                if not knowledge_dir.exists():
                    return ToolResult.ok(invocation.id, f"Knowledge directory '{knowledge_dir}' does not exist yet.")
                files = Knowledge(knowledge_dir).list_files()
                if not files:
                    return ToolResult.ok(invocation.id, "No knowledge pages found.")
                lines = [f"Knowledge pages in {knowledge_dir}:"]
                for f in files:
                    preview = f.get('preview', '')
                    lines.append(f"  {f['name']} ({f['path']})" + (f" — {preview}" if preview else ''))
                return ToolResult.ok(invocation.id, '\n'.join(lines))

            case KnowledgeAction.query:
                if not params.query:
                    return ToolResult.error(invocation.id, "query requires a question.")
                if context is None:
                    return ToolResult.error(invocation.id, "No tool context available.")
                wf_ctx = _make_workflow_context(context)
                result = await KnowledgeQueryWorkflow().execute(
                    WorkflowInvocation(workflow_name='knowledge-query', args={
                        'knowledge_dir': str(knowledge_dir), 'question': params.query,
                    }), wf_ctx
                )
                return ToolResult.ok(invocation.id, result)

            case KnowledgeAction.add:
                if not params.content:
                    return ToolResult.error(invocation.id, "add requires content.")
                knowledge_dir.mkdir(parents=True, exist_ok=True)
                target_name = params.page or 'notes'
                target = knowledge_dir / f'{target_name}.md'
                if target.exists():
                    existing = target.read_text(encoding='utf-8')
                    target.write_text(existing.rstrip() + '\n\n' + params.content.strip() + '\n', encoding='utf-8')
                else:
                    title = target_name.replace('-', ' ').replace('_', ' ').title()
                    target.write_text(f'# {title}\n\n{params.content.strip()}\n', encoding='utf-8')
                return ToolResult.ok(invocation.id, f"Written to '{target.relative_to(knowledge_dir)}'.")

            case KnowledgeAction.log:
                log_path = knowledge_dir / 'log.md'
                if not log_path.exists():
                    return ToolResult.ok(invocation.id, "No log.md — knowledge base has not been ingested yet.")
                return ToolResult.ok(invocation.id, log_path.read_text(encoding='utf-8'))

            case KnowledgeAction.ingest | KnowledgeAction.lint | KnowledgeAction.consolidate:
                if context is None:
                    return ToolResult.error(invocation.id, "No tool context available.")
                wf_ctx = _make_workflow_context(context)
                wf_args = {'knowledge_dir': str(knowledge_dir)}

                match params.action:
                    case KnowledgeAction.ingest:
                        if not params.source:
                            return ToolResult.error(invocation.id, "ingest requires a source (URL, file path, or text).")
                        result = await KnowledgeIngestWorkflow().execute(
                            WorkflowInvocation(workflow_name='knowledge-ingest', args={
                                **wf_args, 'source': params.source, 'source_type': _source_type(params.source),
                            }), wf_ctx
                        )
                    case KnowledgeAction.lint:
                        result = await KnowledgeLintWorkflow().execute(
                            WorkflowInvocation(workflow_name='knowledge-lint', args=wf_args), wf_ctx
                        )
                    case KnowledgeAction.consolidate:
                        result = await KnowledgeConsolidateWorkflow().execute(
                            WorkflowInvocation(workflow_name='knowledge-consolidate', args=wf_args), wf_ctx
                        )
                return ToolResult.ok(invocation.id, result)

            case _:
                return ToolResult.error(invocation.id, f"Unknown action '{params.action}'.")


tool = KnowledgeTool()
