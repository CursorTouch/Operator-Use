from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import acp

if TYPE_CHECKING:
    from program.runtime.service import Runtime

logger = logging.getLogger(__name__)


async def serve_stdio(runtime: Runtime) -> None:
    """
    Run the Operator ACP agent over stdio until stdin closes.

    This is the standard entry point for IDE/CLI integrations (Zed, Claude Code,
    Codex CLI) and for same-machine inter-agent communication via subprocess.

    Stdout is used exclusively for the ACP JSON-RPC framing; all logging must
    go to stderr to avoid corrupting the protocol stream.
    """
    from program.acp.server import OperatorACPAgent
    from program.acp.registry import ACPRegistry

    agent = OperatorACPAgent(runtime)

    registry = ACPRegistry()
    registry.register({
        'agent_id': 'operator',
        'transport': 'stdio',
        'command': 'operator',
        'args': ['acp'],
        'pid': os.getpid(),
    })

    try:
        await acp.run_agent(agent)
    finally:
        registry.unregister('operator')
        logger.debug('ACP stdio server stopped')
