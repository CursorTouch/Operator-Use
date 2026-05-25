"""CDP CrashReportContext Methods Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from program.browser.cdp.protocol.crash_report_context.types import CrashReportContextEntry


class getEntriesReturns(TypedDict):
    entries: List[CrashReportContextEntry]