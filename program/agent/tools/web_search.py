from __future__ import annotations
import httpx
import re
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class WebSearchArgs(BaseModel):
    query: str = Field(
        ...,
        description="The search query. Be specific — include names, versions, or error messages for better results.",
    )
    max_results: int = Field(
        default=10,
        description="Number of results to return (default 10).",
    )

class WebSearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web and return titles, URLs, and snippets. Follow up with web_fetch to read the full content of a result.",
            schema=WebSearchArgs,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        query = invocation.params.get("query")
        max_results = invocation.params.get("max_results", 10)
        
        if not query:
             return ToolResult.error(id=invocation.id, content="Parameter 'query' is required.")

        try:
            # We'll use DuckDuckGo HTML interface for a simple scraper-based search
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                # DuckDuckGo HTML (no-js) version
                response = await client.get(
                    f"https://html.duckduckgo.com/html/?q={query}",
                    headers=headers,
                    follow_redirects=True
                )
                response.raise_for_status()
                html = response.text
                
                # Simple regex-based parsing of the DDG HTML results
                # Each result is roughly: <a class="result__a" href="...">Title</a> ... <a class="result__snippet" ...>Snippet</a>
                results = []
                
                # Match title and URL
                matches = re.findall(r'<a class="result__a" href="([^"]+)">(.*?)</a>', html, re.DOTALL)
                snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                
                for i, (url, title) in enumerate(matches):
                    if i >= max_results:
                        break
                    
                    # Clean up HTML tags from title and snippet
                    clean_title = re.sub(r'<.*?>', '', title).strip()
                    snippet = snippets[i] if i < len(snippets) else ""
                    clean_snippet = re.sub(r'<.*?>', '', snippet).strip()
                    
                    # DDG results often have internal redirect URLs like /l/?kh=-1&uddg=...
                    if url.startswith("/l/?"):
                         match_url = re.search(r'uddg=([^&]+)', url)
                         if match_url:
                             from urllib.parse import unquote
                             url = unquote(match_url.group(1))
                    
                    results.append({
                        "title": clean_title,
                        "url": url,
                        "snippet": clean_snippet
                    })

                if not results:
                    return ToolResult.ok(id=invocation.id, content=f"No results found for: {query}")

                lines = [f"🔍 Web Search Results for: {query}"]
                for idx, result in enumerate(results, start=1):
                    lines.append(f"🔍 {idx}. Title: {result['title']}")
                    lines.append(f"   URL: {result['url']}")
                    if result.get("snippet"):
                        lines.append(f"   Snippet: {result['snippet']}")
                    lines.append("")

                return ToolResult.ok(id=invocation.id, content="\n".join(lines))

        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to search the web: {e}")
