"""Registro de uso da API REST (`/api/v1/*`): um evento por requisição autenticada.

ASGI puro (não mexe nas respostas): mede tempo, status, tamanho da resposta e o consumo da Voyage, e grava
via `CodeIndexService.record_usage`. A ação é o template da rota ("POST /api/v1/search"), não o caminho
cru. Rotas que passam por `audited` (app.py) deixam os parâmetros e a contagem de itens em `request.state`;
nas demais, os parâmetros são os da query string. O MCP registra o próprio uso em mcp.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ...domain.auth import Principal
from ...domain.usage import client_from_user_agent

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1/"
STATE_PARAMS = "usage_params"
STATE_RESULT_COUNT = "usage_result_count"
# Leituras que não são uso do índice: polling que a interface repete sozinha e as telas de observação
# (o próprio dashboard de uso, pessoas, auditoria). Sem isto, olhar o dashboard inflaria o uso.
NOT_USAGE_PREFIXES = ("/api/v1/sync/jobs", "/api/v1/usage", "/api/v1/me/usage", "/api/v1/users", "/api/v1/audit")


class UsageMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return
        svc = getattr(getattr(scope.get("app"), "state", None), "service", None)
        if svc is None:
            await self.app(scope, receive, send)
            return

        status = 500
        size = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        started = time.monotonic()
        with svc.metered() as meter:
            try:
                await self.app(scope, receive, send_wrapper)
            except asyncio.CancelledError:  # cliente desconectou ou servidor desligando: não é um erro de uso
                raise
            except Exception:
                await self._record(svc, scope, status, size, started, meter.snapshot())
                raise
            await self._record(svc, scope, status, size, started, meter.snapshot())

    @staticmethod
    async def _record(svc: Any, scope: Scope, status: int, size: int, started: float, llm_usage: Any) -> None:
        state: dict[str, Any] = scope.get("state") or {}
        principal = state.get("principal")
        if not isinstance(principal, Principal):  # sem credencial válida: não é uso de ninguém
            return
        route = scope.get("route")
        template = getattr(route, "path", None) or scope["path"]
        if scope["method"] == "GET" and template.startswith(NOT_USAGE_PREFIXES):
            return
        params = state.get(STATE_PARAMS)
        if params is None:
            query = scope.get("query_string", b"").decode("latin-1")
            params = dict(parse_qsl(query)) if query else None
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client, version = client_from_user_agent(headers.get("user-agent"))
        try:
            await svc.record_usage(
                principal, transport="http", action=f"{scope['method']} {template}", status=status,
                duration_ms=int((time.monotonic() - started) * 1000), llm_usage=llm_usage,
                result_count=state.get(STATE_RESULT_COUNT), response_bytes=size,
                client=client, client_version=version, params=params,
            )
        except Exception:  # noqa: BLE001 - métrica nunca derruba a requisição
            log.exception("falha ao registrar uso da API")
