"""DuckDuckGo search provider."""

import asyncio
import httpx


def _html_to_markdown(html: str) -> str:
    from markdownify import markdownify

    return markdownify(
        html, heading_style="ATX", strip=["script", "style", "nav", "footer", "header"]
    )


class DDGSSearch:
    """Web search via DuckDuckGo. No API key required."""

    async def search(self, query: str, max_results: int = 10) -> list[dict]:
        from ddgs import DDGS

        try:
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
        except Exception as e:
            raise RuntimeError(f"DuckDuckGo search failed: {e}") from e

        return [
            {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}
            for r in (results or [])
        ]

    async def fetch(self, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            return _html_to_markdown(response.text)
