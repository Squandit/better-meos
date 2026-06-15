"""
Remote entry hosting via ngrok (pyngrok).

Opens a public https tunnel to the local server so entrants can reach the entry
page from anywhere -- including mobile data -- the way the operator's old setup
did. The authtoken (and optional reserved domain) come from the environment so
no secrets are committed:

    NGROK_AUTHTOKEN   your ngrok token (recommended; anonymous tunnels are
                      rate-limited and show an interstitial)
    NGROK_DOMAIN      a reserved/static domain, so the URL is always the same

The tunnel is started on demand from the Setup screen, never automatically.
"""

from __future__ import annotations

import logging

import config

log = logging.getLogger("remote")

_url: str | None = None


def is_configured() -> bool:
    return bool(config.get_str("ngrok_authtoken"))


def url() -> str | None:
    return _url


def start(port: int) -> str:
    """Open (or return the existing) public tunnel to ``port``. Raises on failure."""
    global _url
    if _url:
        return _url
    from pyngrok import ngrok
    token = config.get_str("ngrok_authtoken")
    if token:
        ngrok.set_auth_token(token)
    opts = {"addr": port, "proto": "http"}
    domain = config.get_str("ngrok_domain")
    if domain:
        opts["domain"] = domain
    tunnel = ngrok.connect(**opts)
    _url = tunnel.public_url
    log.info("ngrok tunnel open: %s -> http://localhost:%s", _url, port)
    return _url


def stop() -> None:
    global _url
    try:
        from pyngrok import ngrok
        ngrok.kill()
    except Exception:  # pragma: no cover - best effort
        pass
    _url = None
