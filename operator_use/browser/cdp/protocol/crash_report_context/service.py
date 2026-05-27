"""CDP CrashReportContext Domain"""
from __future__ import annotations
from typing import TYPE_CHECKING
from .methods.service import CrashReportContextMethods
from .events.service import CrashReportContextEvents

if TYPE_CHECKING:
    from ...service import Client

class CrashReportContext(CrashReportContextMethods, CrashReportContextEvents):
    """
    This domain exposes the current state of the CrashReportContext API.
    """
    def __init__(self, client: Client):
        """
        Initialize the CrashReportContext domain.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        CrashReportContextMethods.__init__(self, client)
        CrashReportContextEvents.__init__(self, client)