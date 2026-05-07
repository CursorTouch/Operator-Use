"""PKCE helpers and local OAuth callback server."""

import base64
import hashlib
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def open_browser(url: str) -> None:
    print(f"\n  Opening browser for login...\n  If it doesn't open automatically:\n  {url}\n")
    webbrowser.open(url)


def wait_for_callback(port: int, timeout: int = 120) -> dict[str, str]:
    """Start a local HTTP server on `port`, block until the OAuth redirect arrives.

    Returns the parsed query-string params from the callback URL.
    Raises RuntimeError if nothing arrives before `timeout` seconds.
    """
    captured: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            captured.update({k: v[0] for k, v in qs.items() if v})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Login complete. You may close this tab.</h2>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", port), _Handler)
    t = Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if not captured:
        raise RuntimeError(f"OAuth callback timed out after {timeout}s on port {port}.")
    return captured
