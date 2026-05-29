from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from operator_use.knowledge.prompts import ingest_index, ingest_read, ingest_synthesize
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation


def _extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if 'youtu.be' in host:
        return parsed.path.lstrip('/')
    if 'youtube.com' in host:
        if parsed.path == '/watch':
            return parse_qs(parsed.query)['v'][0]
        if parsed.path.startswith(('/shorts/', '/embed/')):
            return parsed.path.split('/')[2]
    raise ValueError(f"Cannot extract video ID from: {url}")


def _fetch_youtube_content(url: str) -> str:
    video_id = _extract_video_id(url)

    # Metadata via yt-dlp
    title = url
    uploader = ''
    try:
        import yt_dlp
        opts = {'quiet': True, 'skip_download': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', url)
            uploader = info.get('uploader', '')
    except Exception:
        pass

    # Transcript via youtube-transcript-api
    transcript = ''
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        segments = api.fetch(video_id)
        transcript = ' '.join(s.text for s in segments)
    except Exception as exc:
        transcript = f'(transcript unavailable: {exc})'

    lines = [f'# {title}']
    if uploader:
        lines.append(f'**Channel:** {uploader}')
    lines.append(f'**URL:** {url}')
    lines.append('')
    lines.append('## Transcript')
    lines.append(transcript)
    return '\n'.join(lines)


class KnowledgeIngestWorkflow(Workflow):
    name = 'knowledge-ingest'
    description = 'Ingest a source document into the knowledge base.'
    when_to_use = 'Synthesize a source file into knowledge pages.'
    phases = [
        {'name': 'read',       'description': 'Read source document'},
        {'name': 'synthesize', 'description': 'Create folder-based knowledge pages'},
        {'name': 'index',      'description': 'Update index.yaml and log.md'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        source        = ctx.args.get('source', '')
        knowledge_dir = ctx.args.get('knowledge_dir', '')
        source_type   = ctx.args.get('source_type', 'file')

        if source_type == 'text':
            source_content = source
        elif source_type == 'youtube':
            ctx.log(f'Fetching YouTube transcript: {source}')
            source_content = _fetch_youtube_content(source)
        else:
            async with ctx.phase('read'):
                ctx.log(f'Reading source: {source}')
                read_prompt, read_tools = ingest_read(source, source_type)
                source_content = await ctx.agent(read_prompt, tools=read_tools)

        async with ctx.phase('synthesize'):
            ctx.log('Creating knowledge pages...')
            await ctx.agent(ingest_synthesize(knowledge_dir, source_content), tools=['read', 'write'])

        async with ctx.phase('index'):
            ctx.log('Updating index.yaml and log.md...')
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            await ctx.agent(ingest_index(knowledge_dir, source, timestamp), tools=['read', 'write'])

        return f"Ingested '{source}' into knowledge base at '{knowledge_dir}'."
