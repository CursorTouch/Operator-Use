from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from operator_use.knowledge.service import Knowledge
from operator_use.knowledge.workflows.dream import KnowledgeDreamWorkflow
from operator_use.knowledge.workflows.ingest import KnowledgeIngestWorkflow
from operator_use.knowledge.workflows.lint import KnowledgeLintWorkflow
from operator_use.tool.types import Tool, ToolInvocation, ToolKind, ToolResult
from operator_use.workflow.types import WorkflowContext, WorkflowInvocation

if TYPE_CHECKING:
    from operator_use.tool.types import AbortSignal, ToolContext, ToolExecutionUpdateCallback


class KnowledgeSchema(BaseModel):
    action: Literal['list', 'search', 'add', 'ingest', 'lint', 'dream', 'log']
    query: str = ''       # search: keywords to find
    page: str = ''        # add: target page name (no extension)
    content: str = ''     # add: text to append
    source: str = ''      # ingest: path to source file


KnowledgeSchema.model_rebuild()


def _get_knowledge_dir(context: ToolContext | None) -> Path | None:
    if context is None:
        return None
    loader = context.resource_loader
    if loader is None:
        return None
    profile = getattr(loader, '_active_profile', None)
    return profile.knowledge_dir if profile else None


def _make_workflow_context(context: ToolContext) -> WorkflowContext:
    tools = context.engine.tools if context.engine is not None else []
    return WorkflowContext(llm=context.llm, tools=tools)


class KnowledgeTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='knowledge',
            description=(
                'Manage the knowledge base stored in the active profile\'s knowledge/ directory.\n\n'
                'Actions:\n'
                '  list    — list all pages with a one-line preview\n'
                '  search  — find pages containing a keyword (requires: query)\n'
                '  add     — append content to a page, creating it if needed (requires: page, content)\n'
                '  ingest  — synthesize a source file into knowledge pages (requires: source)\n'
                '  lint    — check for contradictions and stale content\n'
                '  dream   — consolidate and deduplicate all pages\n'
                '  log     — return the audit log of past operations'
            ),
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

        # ── list ──────────────────────────────────────────────────────────────

        if params.action == 'list':
            if not knowledge_dir.exists():
                return ToolResult.ok(invocation.id, f"Knowledge directory '{knowledge_dir}' does not exist yet.")
            files = Knowledge(knowledge_dir).list_files()
            if not files:
                return ToolResult.ok(invocation.id, "No knowledge pages found.")
            lines = [f"Knowledge pages in {knowledge_dir}:"]
            for f in files:
                preview = f.get('preview', '')
                lines.append(f"  {f['name']}" + (f" — {preview}" if preview else ''))
            return ToolResult.ok(invocation.id, '\n'.join(lines))

        # ── search ────────────────────────────────────────────────────────────

        if params.action == 'search':
            if not params.query:
                return ToolResult.error(invocation.id, "search requires a query.")
            if not knowledge_dir.exists():
                return ToolResult.ok(invocation.id, "Knowledge directory does not exist yet.")
            q = params.query.lower()
            results: list[str] = []
            for path in sorted(knowledge_dir.rglob('*.md')):
                if path.name == 'log.md':
                    continue
                try:
                    text = path.read_text(encoding='utf-8')
                    if q in text.lower():
                        rel = path.relative_to(knowledge_dir).as_posix()
                        matches = [l.strip() for l in text.splitlines() if q in l.lower()][:3]
                        results.append(f"[{rel}]\n" + '\n'.join(f'  {l}' for l in matches))
                except Exception:
                    pass
            if not results:
                return ToolResult.ok(invocation.id, f"No pages match '{params.query}'.")
            return ToolResult.ok(invocation.id, '\n\n'.join(results))

        # ── add ───────────────────────────────────────────────────────────────

        if params.action == 'add':
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

        # ── log ───────────────────────────────────────────────────────────────

        if params.action == 'log':
            log_path = knowledge_dir / 'log.md'
            if not log_path.exists():
                return ToolResult.ok(invocation.id, "No log.md — knowledge base has not been ingested yet.")
            return ToolResult.ok(invocation.id, log_path.read_text(encoding='utf-8'))

        # ── ingest / lint / dream (internal workflows) ────────────────────────

        if context is None:
            return ToolResult.error(invocation.id, "No tool context available.")

        wf_ctx = _make_workflow_context(context)
        wf_args = {'knowledge_dir': str(knowledge_dir)}

        if params.action == 'ingest':
            if not params.source:
                return ToolResult.error(invocation.id, "ingest requires a source path.")
            wf_args['source'] = params.source
            result = await KnowledgeIngestWorkflow().execute(
                WorkflowInvocation(workflow_name='knowledge-ingest', args=wf_args), wf_ctx
            )
            return ToolResult.ok(invocation.id, result)

        if params.action == 'lint':
            result = await KnowledgeLintWorkflow().execute(
                WorkflowInvocation(workflow_name='knowledge-lint', args=wf_args), wf_ctx
            )
            return ToolResult.ok(invocation.id, result)

        if params.action == 'dream':
            result = await KnowledgeDreamWorkflow().execute(
                WorkflowInvocation(workflow_name='knowledge-dream', args=wf_args), wf_ctx
            )
            return ToolResult.ok(invocation.id, result)

        return ToolResult.error(invocation.id, f"Unknown action '{params.action}'.")


tool = KnowledgeTool()
