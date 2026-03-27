"""Exa search provider. Requires: pip install exa-py"""

import asyncio


class ExaSearch:
    """Neural/semantic web search via Exa. Requires an API key."""

    def __init__(self, api_key: str):
        try:
            from exa_py import Exa
        except ImportError:
            raise ImportError("exa-py is required: pip install exa-py") from None
        self._client = Exa(api_key=api_key)

    async def search(self, query: str, max_results: int = 10) -> list[dict]:
        response = await asyncio.to_thread(
            self._client.search,
            query,
            num_results=max_results,
            use_autoprompt=True,
        )
        results = []
        for r in response.results:
            snippet = ""
            if hasattr(r, "highlights") and r.highlights:
                snippet = r.highlights[0]
            elif hasattr(r, "summary") and r.summary:
                snippet = r.summary
            results.append({"title": r.title or "", "url": r.url, "snippet": snippet})
        return results

    async def fetch(self, url: str) -> str:
        response = await asyncio.to_thread(
            self._client.get_contents,
            [url],
            text=True,
        )
        if response.results:
            return response.results[0].text or ""
        return ""
