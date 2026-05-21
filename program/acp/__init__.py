try:
    from program.acp.server import OperatorACPAgent
    from program.acp.client import OperatorACPClient, ACPClient
    from program.acp.registry import ACPRegistry
    from program.acp.transport.stdio import serve_stdio
    from program.acp.utils import text_from_content_blocks, content_blocks_from_text

    __all__ = [
        'OperatorACPAgent',
        'OperatorACPClient',
        'ACPClient',
        'ACPRegistry',
        'serve_stdio',
        'text_from_content_blocks',
        'content_blocks_from_text',
    ]
except ImportError:
    # acp SDK is an optional dependency — only needed when running the ACP server.
    __all__ = []
