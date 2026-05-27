"""CDP WebMCP Domain Events"""
from __future__ import annotations
from ..types import *
from .types import *
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ....service import Client

class WebMCPEvents:
    """
    Events for the WebMCP domain.
    """
    def __init__(self, client: Client):
        """
        Initialize the WebMCP events.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        self.client = client

    def on_tools_added(self, callback: Callable[[toolsAddedEvent, str | None], None] | None = None) -> None:
        """
    Event fired when new tools are added.    
        Args:
            callback (callable, optional): Function called when the event is fired. 
                The callback receives (params: toolsAddedEvent, session_id: str | None).
        """
        self.client.on('WebMCP.toolsAdded', callback)
    def on_tools_removed(self, callback: Callable[[toolsRemovedEvent, str | None], None] | None = None) -> None:
        """
    Event fired when tools are removed.    
        Args:
            callback (callable, optional): Function called when the event is fired. 
                The callback receives (params: toolsRemovedEvent, session_id: str | None).
        """
        self.client.on('WebMCP.toolsRemoved', callback)
    def on_tool_invoked(self, callback: Callable[[toolInvokedEvent, str | None], None] | None = None) -> None:
        """
    Event fired when a tool invocation starts.    
        Args:
            callback (callable, optional): Function called when the event is fired. 
                The callback receives (params: toolInvokedEvent, session_id: str | None).
        """
        self.client.on('WebMCP.toolInvoked', callback)
    def on_tool_responded(self, callback: Callable[[toolRespondedEvent, str | None], None] | None = None) -> None:
        """
    Event fired when a tool invocation completes or fails.    
        Args:
            callback (callable, optional): Function called when the event is fired. 
                The callback receives (params: toolRespondedEvent, session_id: str | None).
        """
        self.client.on('WebMCP.toolResponded', callback)