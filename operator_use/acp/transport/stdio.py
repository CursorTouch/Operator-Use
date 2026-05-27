from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import acp

if TYPE_CHECKING:
    from operator_use.runtime.service import Runtime

logger = logging.getLogger(__name__)


class ACPStdioServer:
    """
    Serves OperatorACPAgent over stdio for IDE/CLI integrations.

    Intended for Zed, Claude Code CLI, Codex CLI, and same-machine
    inter-agent communication via subprocess.  Stdout is used exclusively
    for ACP JSON-RPC framing; all logging must go to stderr.

    Usage::

        server = ACPStdioServer(runtime)
        await server.serve()
    """

    def __init__(self, runtime: Runtime) -> None:
        from operator_use.acp.server import OperatorACPAgent
        self._agent = OperatorACPAgent(runtime)

    async def serve(self) -> None:
        """Run until stdin closes."""
        try:
            await acp.run_agent(self._agent)
        finally:
            logger.debug('ACP stdio server stopped')


async def serve_stdio(runtime: Runtime) -> None:
    """Convenience wrapper — create ACPStdioServer and serve."""
    await ACPStdioServer(runtime).serve()
