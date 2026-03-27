"""Tavily search provider. Requires: pip install tavily-python"""


class TavilySearch:
    """AI-optimized web search via Tavily. Requires an API key."""

    def __init__(self, api_key: str):
        try:
            from tavily import AsyncTavilyClient
        except ImportError:
            raise ImportError("tavily-python is required: pip install tavily-python") from None
        self._client = AsyncTavilyClient(api_key=api_key)

    async def search(self, query: str, max_results: int = 10) -> list[dict]:
        response = await self._client.search(query, max_results=max_results)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in response.get("results", [])
        ]

    async def fetch(self, url: str) -> str:
        response = await self._client.extract(urls=[url])
        results = response.get("results", [])
        if results:
            return results[0].get("raw_content", "")
        return ""
