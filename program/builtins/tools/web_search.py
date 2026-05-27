import asyncio
from enum import StrEnum
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from ddgs import DDGS


class SearchMode(StrEnum):
    text    = "text"
    news   = "news"
    images = "images"
    videos = "videos"
    books  = "books"


class WebSearchSchema(BaseModel):
    query: str = Field(
        ...,
        description="The search query. Be specific — include names, versions, or error messages for better results.",
    )
    mode: SearchMode = Field(
        default=SearchMode.text,
        description="Search mode: 'text' (default), 'news', 'images', 'videos', or 'books'.",
    )
    max_results: int = Field(
        default=10,
        description="Number of results to return (default 10). Increase to 20+ for broader coverage.",
    )


def _run_search(mode: SearchMode, query: str, max_results: int) -> list[dict]:
    d = DDGS()
    backend="auto"
    match mode:
        case SearchMode.text:
            return d.text(query, region="us-en", safesearch="off", backend=backend, max_results=max_results) or []
        case SearchMode.news:
            return d.news(query, region="us-en", safesearch="off", backend=backend, max_results=max_results) or []
        case SearchMode.images:
            return d.images(query, region="us-en", safesearch="off", backend=backend, max_results=max_results) or []
        case SearchMode.videos:
            return d.videos(query, region="us-en", safesearch="off", backend=backend, max_results=max_results) or []
        case SearchMode.books:
            return d.books(query, max_results=max_results) or []


def _format_results(mode: SearchMode, query: str, results: list[dict]) -> str:
    lines = [f"Search results ({mode}) for: {query}"]
    for idx, r in enumerate(results, start=1):
        match mode:
            case SearchMode.text:
                lines += [f"{idx}. {r.get('title','')}", f"   URL: {r.get('href','')}", f"   {r.get('body','')}"]
            case SearchMode.news:
                lines += [f"{idx}. {r.get('title','')} [{r.get('source','')}] {r.get('date','')}", f"   URL: {r.get('url','')}", f"   {r.get('body','')}"]
            case SearchMode.images:
                lines += [f"{idx}. {r.get('title','')}", f"   Image: {r.get('image','')}", f"   Source: {r.get('url','')}"]
            case SearchMode.videos:
                lines += [f"{idx}. {r.get('title','')} [{r.get('duration','')}]", f"   URL: {r.get('content','')}", f"   {r.get('description','')}"]
            case SearchMode.books:
                lines += [f"{idx}. {r.get('title','')} — {r.get('author','')}", f"   Publisher: {r.get('publisher','')}  {r.get('info','')}", f"   URL: {r.get('url','')}"]
        lines.append("")
    return "\n".join(lines)


class WebSearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_search",
            description=(
                "Search the web and return results. Supports multiple modes: "
                "'text' for pages, 'news' for articles, 'images' for pictures, "
                "'videos' for video content, 'books' for books. "
                "Follow up with web_fetch to read the full content of a result."
            ),
            schema=WebSearchSchema,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Parallel,        )

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        query = invocation.params.get("query")
        mode = SearchMode(invocation.params.get("mode", SearchMode.text))
        max_results = invocation.params.get("max_results", 10)

        if not query:
            return ToolResult.error(id=invocation.id, content="Parameter 'query' is required.")

        try:
            results = await asyncio.to_thread(_run_search, mode, query, max_results)
            if not results:
                return ToolResult.ok(id=invocation.id, content=f"No results found for: {query}")
            return ToolResult.ok(id=invocation.id, content=_format_results(mode, query, results))
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Search failed: {e}")


tool = WebSearchTool()
