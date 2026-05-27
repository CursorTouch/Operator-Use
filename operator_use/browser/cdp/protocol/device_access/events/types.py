"""CDP DeviceAccess Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.browser.cdp.protocol.device_access.types import PromptDevice
    from operator_use.browser.cdp.protocol.device_access.types import RequestId

class deviceRequestPromptedEvent(TypedDict, total=True):
    id: RequestId
    devices: List[PromptDevice]