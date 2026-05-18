# Backwards-compatibility shim.
# New code should import from program.auth.providers directly.
from program.auth.providers import ProviderAuthManager as AuthManager

__all__ = ['AuthManager']
