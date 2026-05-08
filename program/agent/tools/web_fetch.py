from __future__ import annotations
import httpx
import re
from pydantic import BaseModel, Field
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from program.message.types import UserMessage, TextContent, SystemMessage

MAX_TOOL_OUTPUT_LENGTH = 50000 
_EXTRACT_LIMIT = 24_000

class WebFetchArgs(BaseModel):
    url: str = Field(
        ...,
        description="Full URL to fetch (must start with http:// or https://). Redirects are followed automatically.",
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "If provided, the page is passed to the LLM which extracts only the relevant parts. "
            "Use when you know what you're looking for — e.g. 'current temperature in Singapore'. "
        ),
    )
    timeout: int = Field(
        default=10,
        description="Request timeout in seconds (default 10).",
    )

class WebFetchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_fetch",
            description=(
                "Fetch the content of a URL and return it as text. Use after web_search to read a full page. "
                "Set prompt= to extract only what you need from the page — the LLM will filter out irrelevant content."
            ),
            schema=WebFetchArgs,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Parallel
        )

    async def _extract_relevant(self, text: str, prompt: str, llm) -> str:
        """Use LLM to extract the relevant portion of a page for the given prompt."""
        truncated = text[:_EXTRACT_LIMIT]
        messages = [
            SystemMessage(contents=[TextContent(content="You are a precise text extractor. Extract only the information relevant to the user's query from the provided page content. Be concise. If the information is not present, say so clearly.")]),
            UserMessage(contents=[TextContent(content=f"Query: {prompt}\n\nPage content:\n{truncated}")]),
        ]
        try:
            # Using the invoke method of our LLM class
            events = await llm.invoke(messages=messages)
            # Find the final text response
            from program.llm.types import TextEndEvent
            for event in events:
                if isinstance(event, TextEndEvent):
                    return event.text.content
        except Exception:
            pass
        return text

    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        params = invocation.params
        url = params.get("url")
        prompt = params.get("prompt")
        timeout = params.get("timeout", 10)
        
        if not url:
             return ToolResult.error(id=invocation.id, content="Parameter 'url' is required.")

        if not url.startswith(("http://", "https://")):
            return ToolResult.error(id=invocation.id, content=f"Invalid URL: {url}. Must be http:// or https://")

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                
                # Basic HTML to Text
                text = response.text
                text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<.*?>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                
                if not text:
                    return ToolResult.error(id=invocation.id, content=f"No content returned from {url}")

                llm = kwargs.get("_llm")
                if prompt and llm:
                    text = await self._extract_relevant(text, prompt, llm)
                elif len(text) > MAX_TOOL_OUTPUT_LENGTH:
                    text = text[:MAX_TOOL_OUTPUT_LENGTH] + "\n... [Output Truncated]"
                    
                return ToolResult.ok(id=invocation.id, content=text)
        except Exception as e:
            return ToolResult.error(id=invocation.id, content=f"Failed to fetch {url}: {e}")
