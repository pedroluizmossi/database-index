"""CLI: `code-index serve` (API HTTP e MCP em `/mcp`)."""

from __future__ import annotations

import argparse
import logging

from ...settings import Settings
from .common import Subparsers


async def _cmd_serve(a: argparse.Namespace, settings: Settings) -> None:
    import uvicorn

    from ..http.app import create_app, server_config

    host = a.host or settings.http_host
    port = a.port or settings.http_port
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.public_url.startswith("https://"):
        logging.warning("escutando em %s sem CODE_INDEX_PUBLIC_URL https: use um proxy com TLS na frente", host)
    app = create_app(settings)
    await uvicorn.Server(server_config(app, host, port, forwarded_allow_ips=a.forwarded_allow_ips)).serve()


def register(sub: Subparsers, settings: Settings) -> None:
    sv = sub.add_parser("serve", help="sobe a API HTTP e o MCP (/mcp)")
    sv.add_argument("--host", help=f"padrão CODE_INDEX_HTTP_HOST ({settings.http_host})")
    sv.add_argument("--port", type=int, help=f"padrão CODE_INDEX_HTTP_PORT ({settings.http_port})")
    sv.add_argument("--forwarded-allow-ips", default="127.0.0.1",
                    help="IPs do proxy reverso confiáveis para X-Forwarded-* (padrão 127.0.0.1)")
    sv.set_defaults(fn=_cmd_serve)
