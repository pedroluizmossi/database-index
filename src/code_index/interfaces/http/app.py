"""API HTTP (REST em /api/v1, login Google/Microsoft em /auth, MCP em /mcp), num único processo.

Autorização por scope (ver domain/auth.py):
  search:read  GET repos/orgs, busca, leitura de arquivo, MCP
  index:write  sync (job em background)
  admin        gerenciar repos/orgs, auditoria; tokens de outros usuários
Tokens só são emitidos por pessoas logadas no navegador (cookie de sessão): na interface (tokens de agent)
ou pelo OAuth do MCP (/authorize do SDK → /auth/authorize em routes/auth.py: login e consentimento → código → /token).

Aqui só a montagem do app (middlewares, lifespan, rotas por área de routes/, interface web e MCP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from ...services import CodeIndexService, open_service
from ...settings import Settings
from .mcp import build_mcp, transport_security
from .mcp_oauth import pending_signer
from .pages import UI_DIR
from .routes import ROUTERS
from .security import RateLimiter
from .usage import UsageMiddleware

log = logging.getLogger(__name__)


class RegisterRateLimit:
    """Limite por IP no registro de clientes OAuth (/register é aberto, pelo RFC 7591). ASGI puro, para não
    interferir nas respostas do MCP."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter, per_hour: int = 20) -> None:
        self.app, self.limiter, self.per_hour = app, limiter, per_hour

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == "/register":
            ip = (scope.get("client") or ("?",))[0]
            retry = self.limiter.hit("oauth-register", ip, limit=self.per_hour, window=3600.0)
            if retry is not None:
                resp = JSONResponse(
                    {"error": "temporarily_unavailable", "error_description": "muitos registros; tente mais tarde"},
                    status_code=429, headers={"Retry-After": str(int(retry) + 1)},
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


HOUSEKEEPING_INTERVAL_S = 6 * 3600


async def _housekeeping_loop(svc: CodeIndexService) -> None:
    """Limpeza periódica (uso além da retenção, sessões vencidas): ao subir e a cada 6 horas."""
    while True:
        try:
            removed = await svc.housekeeping()
            if any(removed.values()):
                log.info("limpeza: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - tenta de novo no próximo ciclo
            log.exception("limpeza periódica falhou")
        await asyncio.sleep(HOUSEKEEPING_INTERVAL_S)


def create_app(settings: Settings) -> FastAPI:
    secret = settings.require_auth_secret()
    limiter = RateLimiter()
    holder: dict[str, CodeIndexService] = {}

    def service() -> CodeIndexService:
        if "svc" not in holder:
            raise RuntimeError("serviço ainda não iniciado")
        return holder["svc"]

    mcp = build_mcp(settings, service, limiter)
    # Sem estado: as tools são pergunta/resposta, sem notificações do servidor. Com sessão (o padrão do
    # SDK), cada cliente mantinha um stream SSE aberto para sempre: o desligamento ficava esperando por
    # ele, e após um restart os clientes precisavam reconectar (o id de sessão antigo deixava de existir).
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", transport_security=transport_security(settings), host=settings.http_host,
        stateless_http=True, json_response=True,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            svc = await stack.enter_async_context(open_service(settings))
            holder["svc"] = app.state.service = svc
            await svc.recover_interrupted_jobs()  # lease vencido (servidor/worker caiu): volta à fila
            if settings.sync_worker_embedded:
                svc.start_background_worker()  # desligado: só o `code-index worker` executa os jobs
            housekeeping = asyncio.create_task(_housekeeping_loop(svc))
            stack.callback(housekeeping.cancel)
            await stack.enter_async_context(mcp.session_manager.run())
            try:
                yield
            finally:
                holder.pop("svc", None)  # open_service encerra o worker embutido (o job volta à fila)

    app = FastAPI(
        title="code-index",
        version="1",
        description="Índice de código (CocoIndex + Voyage + pgvector). Autentique com Bearer token "
        "ou faça login em /auth/login (Google ou Microsoft).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.limiter = limiter
    app.state.login_signer = URLSafeTimedSerializer(secret, salt="code-index-login")
    app.state.oauth_signer = pending_signer(settings)

    hostnames = {urlsplit(settings.public_url).hostname or "localhost"}
    hostnames |= {h.rsplit(":", 1)[0] for h in settings.allowed_hosts}
    if settings.http_host in ("127.0.0.1", "localhost", "::1"):
        hostnames |= {"127.0.0.1", "localhost", "::1", "testserver"}
    app.add_middleware(UsageMiddleware)  # mais interno: só vê requisições que passaram pelo TrustedHost
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(hostnames))
    app.add_middleware(RegisterRateLimit, limiter=limiter)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    for router in ROUTERS:
        app.include_router(router)

    # Interface web (React, em frontend/): arquivos estáticos sem dados. A página chama /api/v1 com o
    # token digitado pela pessoa. Sem o build, a rota não é montada.
    if UI_DIR.is_dir():
        @app.get("/ui", include_in_schema=False)
        async def ui_redirect() -> RedirectResponse:
            return RedirectResponse("/ui/", status_code=307)

        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
    else:
        log.warning("interface web não montada: %s não existe (rode `pnpm build` em frontend/)", UI_DIR)

    # MCP (Streamable HTTP) em /mcp e metadados OAuth em /.well-known/*: rotas não casadas acima.
    app.mount("/", mcp_app)
    return app


if TYPE_CHECKING:
    import uvicorn

SHUTDOWN_TIMEOUT_S = 5  # desligamento gracioso: conexões abertas além disto são encerradas


def server_config(app: FastAPI, host: str, port: int, *, forwarded_allow_ips: str | None = None,
                  log_level: str = "info") -> "uvicorn.Config":
    """Configuração do uvicorn usada pela CLI (e pelos testes): o mesmo comportamento de desligamento."""
    import uvicorn

    return uvicorn.Config(
        app, host=host, port=port, log_level=log_level, proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips, server_header=False,
        timeout_graceful_shutdown=SHUTDOWN_TIMEOUT_S,
    )
