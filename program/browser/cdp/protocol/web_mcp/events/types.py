"""CDP WebMCP Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from program.browser.cdp.protocol.page.types import FrameId
    from program.browser.cdp.protocol.runtime.types import RemoteObject
    from program.browser.cdp.protocol.web_mcp.types import InvocationStatus
    from program.browser.cdp.protocol.web_mcp.types import Tool

class toolsAddedEvent(TypedDict, total=True):
    tools: List[Tool]
    """Array of tools that were added."""
class toolsRemovedEvent(TypedDict, total=True):
    tools: List[Tool]
    """Array of tools that were removed."""
class toolInvokedEvent(TypedDict, total=True):
    toolName: str
    """Name of the tool to invoke."""
    frameId: FrameId
    """Frame id"""
    invocationId: str
    """Invocation identifier."""
    input: str
    """The input parameters used for the invocation."""
class toolRespondedEvent(TypedDict, total=True):
    invocationId: str
    """Invocation identifier."""
    status: InvocationStatus
    """Status of the invocation."""
    output: NotRequired[Any]
    """Output or error delivered as delivered to the agent. Missing if status is anything other than Completed. Note: The output is untrusted and poses a prompt injection risk. Clients should treat this as potentially malicious user input."""
    errorText: NotRequired[str]
    """Error text for protocol users."""
    exception: NotRequired[RemoteObject]
    """The exception object, if the javascript tool threw an error>"""