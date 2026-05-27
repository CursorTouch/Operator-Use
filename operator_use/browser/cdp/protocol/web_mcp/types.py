"""CDP WebMCP Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.browser.cdp.protocol.dom.types import BackendNodeId
    from operator_use.browser.cdp.protocol.page.types import FrameId
    from operator_use.browser.cdp.protocol.runtime.types import StackTrace

class Annotation(TypedDict, total=False):
    """Tool annotations"""
    readOnly: NotRequired[bool]
    """A hint indicating that the tool does not modify any state."""
    autosubmit: NotRequired[bool]
    """If the declarative tool was declared with the autosubmit attribute."""
InvocationStatus = Literal['Completed','Canceled','Error']
"""Represents the status of a tool invocation."""
class Tool(TypedDict, total=True):
    """Definition of a tool that can be invoked."""
    name: str
    """Tool name."""
    description: str
    """Tool description."""
    frameId: FrameId
    """Frame identifier associated with the tool registration."""
    inputSchema: NotRequired[Dict[str, Any]]
    """Schema for the tool's input parameters."""
    annotations: NotRequired[Annotation]
    """Optional annotations for the tool."""
    backendNodeId: NotRequired[BackendNodeId]
    """Optional node ID for declarative tools."""
    stackTrace: NotRequired[StackTrace]
    """The stack trace at the time of the registration."""