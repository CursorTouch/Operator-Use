"""CDP WebMCP Domain"""
from __future__ import annotations
from typing import TYPE_CHECKING
from .methods.service import WebMCPMethods
from .events.service import WebMCPEvents

if TYPE_CHECKING:
    from ...service import Client

class WebMCP(WebMCPMethods, WebMCPEvents):
    """
    Access the WebMCP domain.
    """
    def __init__(self, client: Client):
        """
        Initialize the WebMCP domain.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        WebMCPMethods.__init__(self, client)
        WebMCPEvents.__init__(self, client)