"""Gateway module: unified channel management and outbound dispatch."""

from operator_use.gateway.service import Gateway
from operator_use.gateway.channels import BaseChannel

__all__ = ["Gateway", "BaseChannel"]
