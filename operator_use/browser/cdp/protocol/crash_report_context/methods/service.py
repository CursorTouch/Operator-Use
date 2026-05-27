"""CDP CrashReportContext Domain Methods"""
from __future__ import annotations
from ..types import *
from .types import *
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ....service import Client

class CrashReportContextMethods:
    """
    Methods for the CrashReportContext domain.
    """
    def __init__(self, client: Client):
        """
        Initialize the CrashReportContext methods.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        self.client = client

    async def get_entries(self, params: getEntriesParameters | None = None, session_id: str | None = None) -> getEntriesReturns:
        """
    Returns all entries in the CrashReportContext across all frames in the page.    
        Args:
            params (getEntriesParameters, optional): Parameters for the getEntries method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    getEntriesReturns: The result of the getEntries call.
        """
        return await self.client.send(method="CrashReportContext.getEntries", params=params, session_id=session_id)