__all__ = [
    'ProviderAuthManager',
    'ChannelAuthManager',
    'ChannelTokens',
    'TelegramAuth',
    'DiscordAuth',
    'SlackAuth',
    'TwitchAuth',
    'ACPAuthManager',
    'AuthCredential',
    'OAuthCredential',
    'APICredential',
    'AuthStatus',
]


def __getattr__(name: str):
    if name == 'ProviderAuthManager':
        from operator_use.auth.providers import ProviderAuthManager
        return ProviderAuthManager
    if name in {'ChannelAuthManager', 'ChannelTokens', 'TelegramAuth', 'DiscordAuth', 'SlackAuth', 'TwitchAuth'}:
        from operator_use.auth import channels
        return getattr(channels, name)
    if name == 'ACPAuthManager':
        from operator_use.auth.acp import ACPAuthManager
        return ACPAuthManager
    if name in {'AuthCredential', 'OAuthCredential', 'APICredential', 'AuthStatus'}:
        from operator_use.auth import types
        return getattr(types, name)
    raise AttributeError(f"module 'program.auth' has no attribute {name!r}")
