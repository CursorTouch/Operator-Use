"""CDP CrashReportContext Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from program.browser.cdp.protocol.page.types import FrameId

class CrashReportContextEntry(TypedDict, total=True):
    """Key-value pair in CrashReportContext."""
    key: str
    value: str
    frameId: FrameId
    """The ID of the frame where the key-value pair was set."""