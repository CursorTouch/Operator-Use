"""CDP WebMCP Domain Methods"""
from __future__ import annotations
from ..types import *
from .types import *
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ....service import Client

class WebMCPMethods:
    """
    Methods for the WebMCP domain.
    """
    def __init__(self, client: Client):
        """
        Initialize the WebMCP methods.
        
        Args:
            client (Client): The parent CDP client instance.
        """
        self.client = client

    async def enable(self, params: enableParameters | None = None, session_id: str | None = None) -> Dict[str, Any]:
        """
    Enables the WebMCP domain, allowing events to be sent. Enabling the domain will trigger a toolsAdded event for all currently registered tools.    
        Args:
            params (enableParameters, optional): Parameters for the enable method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    Dict[str, Any]: The result of the enable call.
        """
        return await self.client.send(method="WebMCP.enable", params=params, session_id=session_id)
    async def disable(self, params: disableParameters | None = None, session_id: str | None = None) -> Dict[str, Any]:
        """
    Disables the WebMCP domain.    
        Args:
            params (disableParameters, optional): Parameters for the disable method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    Dict[str, Any]: The result of the disable call.
        """
        return await self.client.send(method="WebMCP.disable", params=params, session_id=session_id)
    async def invoke_tool(self, params: invokeToolParameters | None = None, session_id: str | None = None) -> invokeToolReturns:
        """
    Invokes a registered tool.    
        Args:
            params (invokeToolParameters, optional): Parameters for the invokeTool method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    invokeToolReturns: The result of the invokeTool call.
        """
        return await self.client.send(method="WebMCP.invokeTool", params=params, session_id=session_id)
    async def cancel_invocation(self, params: cancelInvocationParameters | None = None, session_id: str | None = None) -> Dict[str, Any]:
        """
    Cancels a pending tool invocation.    
        Args:
            params (cancelInvocationParameters, optional): Parameters for the cancelInvocation method.
            session_id (str, optional): Target session ID for flat protocol usage.
            
        Returns:
    Dict[str, Any]: The result of the cancelInvocation call.
        """
        return await self.client.send(method="WebMCP.cancelInvocation", params=params, session_id=session_id)