from __future__ import annotations
import asyncio
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class WebSearchSchema(BaseModel):
    query: str = Field(
        ...,
        description="The search query. Be specific — include names, versions, or error messages for better results.",
    )
    max_results: int = Field(
        default=10,
        description="Number of results to return (default 10). Increase to 20+ when you need broader coverage, decrease to 3-5 for quick lookups.",
    )

class WebSearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web and return titles, URLs, and snippets. Use for current events, documentation, package info, or error messages. Follow up with web_fetch to read the full content of a result.",
            schema=WebSearchSchema,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        query = invocation.params.get("query")
        max_results = invocation.params.get("max_results", 10)
        
        if not query:
             return ToolResult.error(id=invocation.id, content="Parameter 'query' is required.")

        try:
            from ddgs import DDGS
            results = await asyncio.to_thread(
                lambda: DDGS().text(
                    query,
                    region="us-en",
                    safesearch="off",
                    timelimit="3d",
                    backend="auto",
                    max_results=max_results,
                )
            )
            
            if not results:
                return ToolResult.ok(id=invocation.id, content=f"No results found for: {query}")

            lines = [f"Web Search Results for: {query}"]
            for idx, r in enumerate(results, start=1):
                lines.append(f"{idx}. Title: {r['title']}")
                lines.append(f"   URL: {r['href']}")
                if r.get("body"):
                    lines.append(f"   Snippet: {r['body']}")
                lines.append("")

            return ToolResult.ok(id=invocation.id, content="\n".join(lines))
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to search the web: {e}")
