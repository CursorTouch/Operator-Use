from __future__ import annotations

import asyncio
import os
from typing import Any

from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryContext, MemoryOptions, MemorySearchResult


class OpenVikingMemoryAPI(BaseMemoryAPI):
    """OpenViking memory provider using the optional ``openviking`` SDK.

    OpenViking organizes context as a filesystem with tiered loading
    (L0 summary → L1 overview → L2 full). Prefetch and search return the
    light L0/L1 layers so recalled context stays token-cheap; full L2 detail is
    only read on demand.

    Connects to a running OpenViking server when ``OPENVIKING_ENDPOINT`` (or the
    ``endpoint`` config key) is set, otherwise runs embedded against a local data
    directory under the active profile.
    """

    def __init__(self, options: MemoryOptions | None = None, client: Any | None = None) -> None:
        self.options = options or MemoryOptions()
        self.context = MemoryContext()
        self._client = client
        cfg = self.options.config or {}
        self._target_uri = cfg.get("target_uri", "viking://memory/")

    def initialize(self, context: MemoryContext) -> None:
        super().initialize(context)
        if self._client is None:
            self._client = self._create_client(context)

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self.options.prefetch or not query.strip():
            return ""
        results = await asyncio.to_thread(self.search, query, limit=5)
        if not results:
            return ""
        lines = ["## Recalled Memory", "", "Relevant OpenViking context (summaries) for the current turn:"]
        for r in results:
            lines.append(f"- {r.content}")
        return "\n".join(lines)

    def search(self, query: str, *, limit: int = 5) -> list[MemorySearchResult]:
        if self._client is None:
            return []
        raw = self._find(query)
        resources = self._extract_resources(raw)
        results: list[MemorySearchResult] = []
        for item in resources[:limit]:
            uri = self._get_value(item, "uri") or "openviking"
            score = self._get_value(item, "score") or 0.0
            content = self._summary(item, uri)
            results.append(MemorySearchResult(
                source=str(uri),
                content=str(content),
                score=float(score) if isinstance(score, int | float) else 0.0,
                metadata=item if isinstance(item, dict) else {"uri": str(uri)},
            ))
        return results

    async def remember(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        if self._client is None or not content.strip():
            return ""
        return await asyncio.to_thread(self._remember, content) or self._target_uri

    async def on_turn_complete(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self.options.sync_turns or self._client is None:
            return
        content = f"User: {user_content}\nAssistant: {assistant_content}"
        await asyncio.to_thread(self._remember, content)

    async def shutdown(self) -> None:
        client = self._client
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _create_client(self, context: MemoryContext) -> Any:
        try:
            import openviking as ov  # type: ignore
        except Exception as exc:
            raise ImportError("openviking provider requires the optional 'openviking' package.") from exc

        cfg = self.options.config or {}
        endpoint = cfg.get("endpoint") or os.environ.get("OPENVIKING_ENDPOINT")
        api_key = os.environ.get(self.options.api_key_env or "OPENVIKING_API_KEY")
        if endpoint:
            kwargs: dict[str, Any] = {"endpoint": endpoint}
            if api_key:
                kwargs["api_key"] = api_key
            client = ov.OpenViking(**kwargs)
        else:
            root = self.options.root_dir or context.project_memory_dir
            if root is None:
                raise ValueError(
                    "openviking provider needs OPENVIKING_ENDPOINT or an active "
                    "profile for embedded mode."
                )
            data_dir = str(root / "openviking")
            client = ov.OpenViking(path=data_dir)
        initialize = getattr(client, "initialize", None)
        if initialize is not None:
            initialize()
        return client

    def _find(self, query: str) -> Any:
        client = self._client
        if client is None:
            return None
        try:
            return client.find(query, target_uri=self._target_uri)
        except TypeError:
            return client.find(query)

    def _remember(self, content: str) -> str:
        client = self._client
        if client is None:
            return ""
        for method_name in ("remember", "write"):
            method = getattr(client, method_name, None)
            if method is None:
                continue
            try:
                result = method(content, target=self._target_uri)
            except TypeError:
                result = method(content)
            return str(self._get_value(result, "uri") or result or "")
        # Fall back to resource ingestion when no direct write method exists.
        add = getattr(client, "add_resource", None)
        if add is not None:
            result = add(content)
            return str(self._get_value(result, "root_uri") or "")
        return ""

    def _summary(self, item: Any, uri: str) -> str:
        # Prefer the light tiers (L0/L1) to keep recalled context token-cheap.
        for key in ("l0", "abstract", "summary", "l1", "overview", "text", "content"):
            value = self._get_value(item, key)
            if value:
                return str(value)
        for method_name in ("overview", "abstract", "read"):
            method = getattr(self._client, method_name, None)
            if method is None:
                continue
            try:
                value = method(uri)
            except Exception:
                continue
            if value:
                return str(value)
        return str(uri)

    def _extract_resources(self, raw: Any) -> list[Any]:
        if raw is None:
            return []
        value = getattr(raw, "resources", None)
        if isinstance(value, list):
            return value
        if isinstance(raw, dict):
            for key in ("resources", "results", "matches"):
                inner = raw.get(key)
                if isinstance(inner, list):
                    return inner
        if isinstance(raw, list):
            return raw
        return []

    def _get_value(self, item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)
