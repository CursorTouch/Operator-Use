"""CDP WebMCP Methods Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.browser.cdp.protocol.page.types import FrameId



class invokeToolParameters(TypedDict, total=True):
    frameId: FrameId
    """Frame in which to invoke the tool."""
    toolName: str
    """Name of the tool to invoke."""
    input: Dict[str, Any]
    """Input parameters for the tool, matching the tool's inputSchema."""
class cancelInvocationParameters(TypedDict, total=True):
    invocationId: str
    """Invocation identifier to cancel."""


class invokeToolReturns(TypedDict):
    invocationId: str
    """Unique identifier for this invocation. Response is sent before tool events."""