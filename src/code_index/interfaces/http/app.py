"""API HTTP (REST em /api/v1, login Google/Microsoft em /auth, MCP em /mcp), num único processo.

Autorização por scope (ver domain/auth.py):
  search:read  GET repos/orgs, busca, leitura de arquivo, MCP
  index:write  sync (job em background)
  admin        gerenciar repos/orgs, auditoria; tokens de outros usuários
Tokens só são emitidos por pessoas logadas no navegador (cookie de sessão): na interface (tokens de agent)
ou pelo OAuth do MCP (/authorize do SDK → /auth/authorize aqui: login e consentimento → código → /token).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from mcp.server.auth.provider import construct_redirect_uri
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from ...domain.relations import origins_payload
from ...domain.auth import ADMIN, ENRICH_WRITE, INDEX_WRITE, SEARCH_READ, Principal, session_provider
from ...infra.login import configured_providers, open_provider
from ...infra.oidc import LoginError, OIDCProvider
from ...services import CodeIndexService, open_service
from ...settings import Settings
from ...domain.usage import result_count
from .mcp import build_mcp, transport_security
from .usage import STATE_PARAMS, STATE_RESULT_COUNT, UsageMiddleware
from .mcp_oauth import load_pending, pending_signer
from .security import (
    SESSION_COOKIE,
    RateLimiter,
    check_csrf,
    client_ip,
    enforce_rate_limit,
    current_principal,
    get_service,
    require,
)

log = logging.getLogger(__name__)
LOGIN_COOKIE = "cix_login"

_PAGE_CSS = (
    "body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:24px;"
    "font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#fafafa;color:#18181b}"
    "main{max-width:440px;background:#fff;border:1px solid #e4e4e7;border-radius:16px;padding:28px}"
    "h1{font-size:20px;margin:0 0 8px}p{margin:8px 0;color:#52525b}"
    "a{color:#4f46e5;font-weight:500}code{font-size:14px}"
    ".btn{display:block;box-sizing:border-box;width:100%;margin:10px 0;padding:11px 16px;border-radius:10px;"
    "border:1px solid #d4d4d8;background:#fff;color:#18181b;font:inherit;font-weight:500;text-align:center;"
    "text-decoration:none;cursor:pointer}.btn.primary{background:#4f46e5;border-color:#4f46e5;color:#fff}"
    "@media (prefers-color-scheme:dark){body{background:#09090b;color:#fafafa}"
    "main{background:#18181b;border-color:#27272a}p{color:#a1a1aa}"
    ".btn{background:#27272a;border-color:#3f3f46;color:#fafafa}.btn.primary{background:#4f46e5;color:#fff}}"
)


def _html_page(status_code: int, title: str, inner_html: str) -> HTMLResponse:
    """Página simples do servidor (login, consentimento). `inner_html` já deve vir com o texto escapado.

    Sem `form-action` na CSP: depois do consentimento o navegador segue o redirect para o cliente MCP
    (ex.: http://localhost:PORTA), e o Chrome aplica `form-action` também ao redirect."""
    body = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_PAGE_CSS}</style></head><body><main>"
        f"<h1>{escape(title)}</h1>{inner_html}</main></body></html>"
    )
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                                       "frame-ancestors 'none'",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        },
    )


def _login_page(status_code: int, title: str, message: str, *, retry: bool = True) -> HTMLResponse:
    """Página de erro do login. Todo texto passa por escape: nada vindo de fora vira HTML."""
    retry_link = '<a href="/auth/login">Tentar de novo</a> · ' if retry else ""
    return _html_page(
        status_code, title,
        f"<p>{escape(message)}</p><p>{retry_link}<a href=\"/ui/\">Voltar para a interface</a></p>",
    )


def _chooser_page(providers: list[type[OIDCProvider]], next_path: str | None) -> HTMLResponse:
    """Escolha do provedor quando há mais de um (Google e Microsoft)."""
    links = []
    for p in providers:
        query = urlencode({"provider": p.name, **({"next": next_path} if next_path else {})})
        links.append(f'<a class="btn" href="/auth/login?{escape(query)}">Entrar com {escape(p.label)}</a>')
    return _html_page(200, "Entrar no code-index", "<p>Escolha a sua conta:</p>" + "".join(links))


def _consent_page(*, client_name: str, redirect_uri: str, email: str, req: str) -> HTMLResponse:
    """Consentimento do OAuth do MCP. O nome do cliente vem do próprio cliente (não confiável): só escapado."""
    target = urlsplit(redirect_uri)
    switch = urlencode({"next": f"/auth/authorize?req={req}"})
    return _html_page(
        200, "Autorizar acesso",
        f"<p><strong>{escape(client_name)}</strong> quer acesso de <strong>leitura</strong> ao code-index "
        f"(buscar e ler o código e a documentação indexados) em nome de <strong>{escape(email)}</strong>.</p>"
        f"<p>Depois de aprovar, você volta para <code>{escape(target.scheme)}://{escape(target.netloc)}</code>. "
        "Se não foi você que iniciou esta conexão, negue.</p>"
        '<form method="post" action="/auth/authorize">'
        f'<input type="hidden" name="req" value="{escape(req)}">'
        '<button class="btn primary" name="decision" value="allow">Permitir</button>'
        '<button class="btn" name="decision" value="deny">Negar</button></form>'
        f'<p><a href="/auth/login?{escape(switch)}">Usar outra conta</a></p>',
    )


def _safe_next(value: str | None) -> str | None:
    """Destino depois do login: só a página de consentimento do OAuth (evita open redirect)."""
    if value and value.startswith("/auth/authorize?req=") and "//" not in value and "\\" not in value:
        return value
    return None


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
LOGIN_MAX_AGE = 600
UI_DIR = Path(__file__).resolve().parents[4] / "frontend" / "dist"  # build de `pnpm build` em frontend/


# ------------------------------------------------------------------ modelos de entrada


class UserSettingsIn(BaseModel):
    settings: dict[str, Any] = Field(description="chave → valor; null volta ao padrão", max_length=50)


class RepoIn(BaseModel):
    source: str = Field(description="URL git, 'org/repo' do GitHub ou diretório local do servidor")
    name: str | None = None
    branch: str | None = None
    include: list[str] | None = None
    extra_exclude: list[str] | None = None


class OrgIn(BaseModel):
    name: str
    match: list[str] = []
    skip: list[str] = []
    languages: list[str] = []
    include_archived: bool = False
    include_forks: bool = False
    max_size_mb: int | None = None
    extra_exclude: list[str] = []


class OrgPatch(BaseModel):
    languages: list[str] | None = None
    match: list[str] | None = None
    skip: list[str] | None = None
    max_size_mb: int | None = Field(None, description="0 remove o limite")


class EnrichNextIn(BaseModel):
    kind: Literal["card", "doc_links", "file_summaries"] = "card"
    repo: str | None = Field(None, max_length=300, description="só este repo (doc_links: a fonte de documentação)")
    scope: Literal["todas", "sem-vinculo", "varios"] = Field("todas", description="doc_links: quais páginas")


class SyncIn(BaseModel):
    repos: list[str] | None = Field(None, description="nomes a indexar; vazio = lote automático")
    batch: int | None = Field(None, ge=0, le=50, description="tamanho do lote (padrão do servidor)")
    discover: bool = True
    pull: bool = True
    dry_run: bool = False


class OpenApiSelection(BaseModel):
    domain: str = Field(min_length=1, max_length=100)
    services: list[str] | None = None


class GrepIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    repos: list[str] | None = None
    path_glob: str | None = None
    regex: bool = False
    case_sensitive: bool = False
    max_results: int = Field(50, ge=1, le=500)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    repos: list[str] | None = None
    language: str | None = None
    path_glob: str | None = None
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    rerank: bool = True
    kind: Literal["code", "doc"] | None = None
    include_linked_docs: bool = True
    exclude_repos: list[str] | None = Field(None, max_length=50)
    exclude_path_glob: list[str] | None = Field(None, max_length=50)
    max_docs: int | None = Field(None, ge=0, le=50)
    domain: str | None = Field(None, max_length=64)
    service: str | None = Field(None, max_length=64)


class OwnerIn(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=130, description="'dominio' ou 'dominio/servico'")
    path: str | None = Field(None, max_length=500, description="pasta do repo (monorepo); só com um repo")
    create: bool = Field(False, description="cria o domínio/serviço se não existir no catálogo")
    reason: str = Field("", max_length=500)


class OwnerClearIn(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=500)
    path: str | None = Field(None, max_length=500)


class DocLinkIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
    reason: str = Field("", max_length=500)


class SuggestionReviewIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
    approve: bool


class DocSelectionIn(BaseModel):
    pages: list[str] = Field(max_length=50_000, description="caminhos de página sem idioma, ex.: Plataforma/Email")
    dry_run: bool = Field(False, description="só calcula as regras, sem gravar")


class DocIgnoreIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    reason: str = Field("", max_length=500)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = [SEARCH_READ]
    ttl_days: int | None = Field(None, ge=1, le=365)


# ------------------------------------------------------------------ app


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    if isinstance(e, RuntimeError):
        return HTTPException(status.HTTP_409_CONFLICT, str(e))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


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
            await svc.recover_interrupted_jobs()  # jobs órfãos de uma execução anterior do servidor
            housekeeping = asyncio.create_task(_housekeeping_loop(svc))
            stack.callback(housekeeping.cancel)
            await stack.enter_async_context(mcp.session_manager.run())
            try:
                yield
            finally:
                holder.pop("svc", None)  # open_service encerra os jobs em andamento

    app = FastAPI(
        title="code-index",
        version="1",
        description="Índice de código (CocoIndex + Voyage + pgvector). Autentique com Bearer token "
        "ou faça login em /auth/login (Google ou Microsoft).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.limiter = limiter

    hostnames = {urlsplit(settings.public_url).hostname or "localhost"}
    hostnames |= {h.rsplit(":", 1)[0] for h in settings.allowed_hosts}
    if settings.http_host in ("127.0.0.1", "localhost", "::1"):
        hostnames |= {"127.0.0.1", "localhost", "::1", "testserver"}
    app.add_middleware(UsageMiddleware)  # mais interno: só vê requisições que passaram pelo TrustedHost
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(hostnames))
    app.add_middleware(RegisterRateLimit, limiter=limiter)

    signer = URLSafeTimedSerializer(secret, salt="code-index-login")
    oauth_signer = pending_signer(settings)

    async def audited(request: Request, principal: Principal | None, action: str, params: dict[str, Any] | None,
                      fn: Any) -> Any:
        svc = get_service(request)
        setattr(request.state, STATE_PARAMS, params)  # para o registro de uso (UsageMiddleware)
        try:
            result = await fn
        except (ValueError, RuntimeError, PermissionError) as e:
            err = _http_error(e)
            await svc.audit(principal, action, params=params, status=err.status_code, client_ip=client_ip(request))
            raise err from None
        await svc.audit(principal, action, params=params, status=200, client_ip=client_ip(request))
        setattr(request.state, STATE_RESULT_COUNT, result_count(result))
        return result

    # ---------------------------------------------------------- saúde / login

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/providers", tags=["auth"], summary="Provedores de login ativos (Google, Microsoft)")
    async def login_providers() -> list[dict[str, str]]:
        return [{"name": p.name, "label": p.label} for p in configured_providers(settings)]

    @app.get("/auth/login", tags=["auth"], summary="Login no navegador (Google ou Microsoft; redireciona)")
    async def login(provider: str = "", next_path: str = Query("", alias="next")) -> Response:
        nxt = _safe_next(next_path)
        if not provider:
            available = configured_providers(settings)
            if not available:
                return _login_page(503, "Login indisponível", "Nenhum login configurado neste servidor: defina "
                                   "GOOGLE_CLIENT_ID/SECRET e/ou AZURE_TENANT_ID/CLIENT_ID/SECRET.", retry=False)
            if len(available) > 1:
                return _chooser_page(available, nxt)
            provider = available[0].name
        try:
            oidc = open_provider(settings, provider)
        except RuntimeError as e:
            return _login_page(503, "Login indisponível", str(e), retry=False)
        start = oidc.start()
        resp = RedirectResponse(start.url, status_code=302)
        resp.set_cookie(
            LOGIN_COOKIE,
            signer.dumps({"s": start.state, "n": start.nonce, "v": start.code_verifier, "p": oidc.name, "x": nxt}),
            max_age=LOGIN_MAX_AGE, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/auth",
        )
        return resp

    @app.get("/auth/callback", tags=["auth"], include_in_schema=False)
    async def callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
        svc = get_service(request)
        if error:
            return _login_page(400, "Login cancelado", f"O provedor de login não concluiu o login ({error}).")
        try:
            data = signer.loads(request.cookies.get(LOGIN_COOKIE, ""), max_age=LOGIN_MAX_AGE)
        except BadSignature:
            return _login_page(400, "Login expirou", "A sessão de login venceu ou não foi iniciada aqui.")
        if not code or not secrets.compare_digest(state.encode(), str(data["s"]).encode()):
            return _login_page(400, "Login recusado", "A resposta do provedor não corresponde a este login.")
        try:
            oidc = open_provider(settings, data.get("p", "google"))
            identity = await oidc.finish(code=code, code_verifier=data["v"], nonce=data["n"])
        except RuntimeError as e:
            return _login_page(503, "Login indisponível", str(e), retry=False)
        except LoginError as e:
            await svc.audit(None, "auth:login", params={"provider": data.get("p"), "error": str(e)}, status=403,
                            client_ip=client_ip(request))
            return _login_page(403, "Acesso negado", str(e), retry=False)
        try:
            token, info = await svc.start_session(identity.email, identity.provider, display_name=identity.name)
        except PermissionError as e:  # usuário desativado por um admin
            await svc.audit(None, "auth:login", params={"email": identity.email, "error": str(e)}, status=403,
                            client_ip=client_ip(request))
            return _login_page(403, "Acesso negado", str(e), retry=False)
        await svc.audit(None, "auth:login",
                        params={"email": identity.email, "provider": identity.provider, "scopes": list(info.scopes)},
                        status=200, client_ip=client_ip(request))
        # Volta para o consentimento do OAuth (se o login veio de lá), para a interface web se ela estiver
        # construída ou, senão, para a documentação da API.
        target = _safe_next(data.get("x")) or ("/ui/" if UI_DIR.is_dir() else "/docs")
        resp = RedirectResponse(target, status_code=302)
        resp.delete_cookie(LOGIN_COOKIE, path="/auth")
        resp.set_cookie(
            SESSION_COOKIE, token, max_age=settings.session_ttl_hours * 3600, httponly=True,
            secure=settings.secure_cookies, samesite="lax", path="/",
        )
        return resp

    # ---------------------------------------------------------- OAuth do MCP: login + consentimento

    async def session_principal(request: Request) -> Principal | None:
        """Pessoa logada no navegador (só o cookie de sessão vale aqui, nunca um Bearer)."""
        token = request.cookies.get(SESSION_COOKIE)
        principal = await get_service(request).authenticate(token) if token else None
        return principal if principal is not None and principal.kind == "session" else None

    def to_login(req: str) -> RedirectResponse:
        return RedirectResponse("/auth/login?" + urlencode({"next": f"/auth/authorize?req={req}"}), status_code=303)

    expired_request = ("Autorização expirou", "O pedido de acesso venceu ou foi alterado. "
                       "Conecte o cliente MCP de novo.")

    @app.get("/auth/authorize", tags=["auth"], include_in_schema=False)
    async def oauth_consent(request: Request, req: str = "") -> Response:
        pending = load_pending(oauth_signer, req)
        if pending is None:
            return _login_page(400, *expired_request, retry=False)
        principal = await session_principal(request)
        if principal is None:
            return to_login(req)
        client = await get_service(request).get_oauth_client(pending["client_id"])
        if client is None:
            return _login_page(400, *expired_request, retry=False)
        name = str(client[0].get("client_name") or "Um cliente MCP")
        return _consent_page(client_name=name, redirect_uri=pending["redirect_uri"], email=principal.subject, req=req)

    @app.post("/auth/authorize", tags=["auth"], include_in_schema=False)
    async def oauth_decide(request: Request) -> Response:
        check_csrf(request, settings)  # formulário da nossa própria origem
        form = await request.form()
        req, decision = str(form.get("req", "")), str(form.get("decision", ""))
        pending = load_pending(oauth_signer, req)
        if pending is None:
            return _login_page(400, *expired_request, retry=False)
        principal = await session_principal(request)
        if principal is None:
            return to_login(req)
        svc = get_service(request)
        redirect_uri, state, client_id = pending["redirect_uri"], pending.get("state"), pending["client_id"]
        if decision != "allow":
            await svc.audit(principal, "oauth:authorize", params={"client_id": client_id, "decision": "deny"},
                            status=403, client_ip=client_ip(request))
            return RedirectResponse(construct_redirect_uri(redirect_uri, error="access_denied", state=state),
                                    status_code=303)
        try:
            code = await svc.issue_oauth_code(
                principal, client_id=client_id, scopes=pending.get("scopes"),
                code_challenge=pending["code_challenge"], redirect_uri=redirect_uri,
                redirect_uri_explicit=bool(pending.get("redirect_uri_explicit")), resource=pending.get("resource"),
            )
        except (ValueError, PermissionError) as e:
            return _login_page(403, "Acesso negado", str(e), retry=False)
        await svc.audit(principal, "oauth:authorize", params={"client_id": client_id, "decision": "allow"},
                        status=200, client_ip=client_ip(request))
        return RedirectResponse(construct_redirect_uri(redirect_uri, code=code, state=state), status_code=303)

    @app.post("/auth/logout", tags=["auth"], summary="Encerra a sessão")
    async def logout(request: Request, principal: Principal = Depends(current_principal)) -> Response:
        svc = get_service(request)
        await svc.end_session(principal)
        await svc.audit(principal, "auth:logout", client_ip=client_ip(request))
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @app.get("/api/v1/me", tags=["auth"], summary="Quem sou eu")
    async def me(principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return {"subject": principal.subject, "kind": principal.kind, "scopes": sorted(principal.scopes),
                "token_name": principal.token_name, "provider": session_provider(principal)}

    @app.get("/api/v1/me/settings", tags=["auth"], summary="Minhas configurações (valores, padrões e schema)")
    async def my_settings(request: Request, principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).user_settings(principal)

    @app.patch("/api/v1/me/settings", tags=["auth"],
               summary="Altera minhas configurações (só com login no navegador; null volta ao padrão)")
    async def update_my_settings(body: UserSettingsIn, request: Request,
                                 principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        result: dict[str, Any] = await audited(request, principal, "settings:update", {"keys": sorted(body.settings)},
                                               get_service(request).update_user_settings(principal, body.settings))
        return result

    # ---------------------------------------------------------- consulta

    @app.post("/api/v1/search", tags=["busca"], summary="Busca híbrida + rerank")
    async def search(body: SearchIn, request: Request,
                     principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> list[dict[str, Any]]:
        svc = get_service(request)
        hits = await audited(request, principal, "search", body.model_dump(exclude={"rerank"}), svc.search(
            body.query, top_k=body.top_k, repos=body.repos, language=body.language, path_glob=body.path_glob,
            mode=body.mode, rerank=body.rerank, kind=body.kind, include_linked_docs=body.include_linked_docs,
            exclude_repos=body.exclude_repos, exclude_path_glob=body.exclude_path_glob, max_docs=body.max_docs,
            domain=body.domain, service=body.service,
        ))
        return [h.to_dict() for h in hits]

    @app.get("/api/v1/symbols", tags=["busca"], summary="Onde um símbolo é declarado")
    async def symbols(request: Request, name: str = Query(min_length=1, max_length=200), kind: str | None = None,
                      repo: list[str] | None = Query(None), language: str | None = None,
                      limit: int = Query(20, ge=1, le=200),
                      principal: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        hits = await audited(request, principal, "symbols", {"name": name, "kind": kind, "repo": repo},
                             get_service(request).find_symbol(name, kind=kind, repos=repo, language=language,
                                                              limit=limit))
        return [h.to_dict() for h in hits]

    @app.post("/api/v1/grep", tags=["busca"], summary="Texto exato ou regex nos repositórios")
    async def grep(body: GrepIn, request: Request,
                   principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> dict[str, Any]:
        res = await audited(request, principal, "grep", body.model_dump(exclude={"case_sensitive"}),
                            get_service(request).grep(body.pattern, repos=body.repos, path_glob=body.path_glob,
                                                      regex=body.regex, case_sensitive=body.case_sensitive,
                                                      max_results=body.max_results))
        return {"matches": [m.to_dict() for m in res.matches], "truncated": res.truncated}

    @app.get("/api/v1/files/{repo}/{path:path}", tags=["busca"], summary="Lê arquivo (segredos mascarados)")
    async def read_file(repo: str, path: str, request: Request, start_line: int | None = Query(None, ge=1),
                        end_line: int | None = Query(None, ge=1),
                        principal: Principal = Depends(require(SEARCH_READ))) -> Response:
        svc = get_service(request)
        try:
            text = await svc.read_file(repo, path, start_line, end_line)
        except ValueError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
        return Response(text, media_type="text/plain; charset=utf-8")

    @app.get("/api/v1/repos/{name}/files", tags=["repos"],
             summary="Arquivos indexados de um repo, com o resumo de cada um (escrito por agent), se houver")
    async def repo_files(name: str, request: Request,
                         _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).repo_files(name)

    @app.get("/api/v1/summaries/coverage", tags=["busca"],
             summary="Cobertura dos resumos de arquivos de código (total e por repo)")
    async def summary_coverage(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).summary_coverage()

    @app.get("/api/v1/summaries/repo/{repo}", tags=["busca"], summary="Resumos dos arquivos de um repo")
    async def repo_summaries(repo: str, request: Request,
                             _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).repo_summaries(repo)

    @app.get("/api/v1/summaries/file/{repo}/{path:path}", tags=["busca"],
             summary="Resumo de um arquivo escrito por agent (null se não houver)")
    async def file_summary(repo: str, path: str, request: Request,
                           _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any] | None:
        return await get_service(request).file_summary(repo, path)

    # ---------------------------------------------------------- repos

    @app.get("/api/v1/repos", tags=["repos"])
    async def list_repos(request: Request, status_: str | None = Query(None, alias="status"), org: str | None = None,
                         name: str | None = None, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        rows = await get_service(request).list_repos(status=status_, org=org, name=name)
        return [r.to_dict() for r in rows]

    # ---------------------------------------------------------- dono no DDD (domínio/serviço)

    @app.get("/api/v1/ownership/catalog", tags=["dono"],
             summary="Domínios e serviços (do SDL e criados à mão), repos por serviço e cobertura")
    async def ownership_catalog(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).ownership_catalog()

    @app.get("/api/v1/ownership", tags=["dono"],
             summary="Dono de cada repo (e pasta de monorepo), com origem e alternativas")
    async def ownership_list(request: Request, domain: str | None = Query(None, max_length=64),
                             service: str | None = Query(None, max_length=64),
                             origin: str | None = Query(None, max_length=20), unowned: bool = False,
                             repo: str | None = Query(None, max_length=200),
                             _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        try:
            return await get_service(request).ownership_list(domain=domain, service=service, origin=origin,
                                                              unowned=unowned, repo=repo)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.put("/api/v1/ownership", tags=["dono"], summary="Define o dono manual de repos ou de uma pasta (admin)")
    async def set_owner(body: OwnerIn, request: Request,
                        principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        return await audited(request, principal, "owner:set:api", body.model_dump(exclude={"repos"}),
                             get_service(request).set_owner(principal, body.repos, body.owner, path=body.path,
                                                            create=body.create, reason=body.reason))

    @app.post("/api/v1/ownership/clear", tags=["dono"],
              summary="Remove o dono manual (volta ao inferido) de repos ou de uma pasta (admin)")
    async def clear_owner(body: OwnerClearIn, request: Request,
                          principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        return await audited(request, principal, "owner:clear:api", body.model_dump(exclude={"repos"}),
                             get_service(request).clear_owner(principal, body.repos, path=body.path))

    @app.get("/api/v1/repos/find", tags=["repos"],
             summary="Repos cujo cartão casa com a pergunta (full-text, sem custo de API)")
    async def find_repos(request: Request, q: str = Query(..., min_length=1, max_length=500),
                         limit: int = Query(10, ge=1, le=50),
                         _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        try:
            return await get_service(request).find_repos(q, limit=limit)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.get("/api/v1/api-impls", tags=["openapi"],
             summary="Onde uma operação da API é implementada (handler do .sdl; sem custo)")
    async def api_impls(request: Request, operation: str = Query(..., min_length=1, max_length=300),
                        service: str | None = None, domain: str | None = None,
                        limit: int = Query(20, ge=1, le=100),
                        _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        try:
            return await get_service(request).find_api_impl(operation, service=service, domain=domain, limit=limit)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.get("/api/v1/repos/{name}/card", tags=["repos"],
             summary="Cartão do repo (resumo escrito por agent) e fatos extraídos sem LLM")
    async def repo_card(name: str, request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        try:
            info = await get_service(request).repo_card(name)
        except ValueError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
        if info is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sem cartão nem fatos para este repo")
        return info

    @app.get("/api/v1/docs/{name}/preview", tags=["docs"],
             summary="Prévia das páginas de uma fonte de documentação (sem custo)")
    async def docs_preview(name: str, request: Request, limit: int = Query(200, ge=1, le=1000),
                           _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).docs_preview(name, limit=limit)

    @app.get("/api/v1/docs/sources", tags=["docs"],
             summary="Fontes de documentação: seleção (include/exclude) e documentos no índice")
    async def doc_sources(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).doc_sources()

    @app.get("/api/v1/docs/{name}/catalog", tags=["docs"],
             summary="Todas as páginas da wiki, marcando as da seleção e as do índice (admin; sem custo)")
    async def doc_catalog(name: str, request: Request, _: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        try:
            return await get_service(request).wiki_catalog(name)
        except (ValueError, RuntimeError) as e:
            raise _http_error(e) from None

    @app.put("/api/v1/docs/{name}/selection", tags=["docs"],
             summary="Troca as páginas escolhidas da wiki (admin; não indexa, vale no próximo sync)")
    async def doc_selection(name: str, body: DocSelectionIn, request: Request,
                            principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        params = {"name": name, "pages": len(body.pages), "dry_run": body.dry_run}
        return await audited(request, principal, "docs:selection", params, get_service(request).set_wiki_selection(
            name, body.pages, dry_run=body.dry_run))

    @app.post("/api/v1/docs/ignored", tags=["docs"], summary="Tira uma página da busca, sem tirar do índice (admin)")
    async def ignore_doc(body: DocIgnoreIn, request: Request,
                         principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        return await audited(request, principal, "docs:ignore", body.model_dump(),
                             get_service(request).ignore_doc_key(body.doc_repo, body.doc_path, body.reason))

    @app.delete("/api/v1/docs/ignored", tags=["docs"], summary="Devolve uma página à busca (admin)")
    async def unignore_doc(request: Request, doc_repo: str = Query(..., max_length=200),
                           doc_path: str = Query(..., max_length=1000),
                           principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
        params = {"doc_repo": doc_repo, "doc_path": doc_path}
        removed = await audited(request, principal, "docs:unignore", params,
                                get_service(request).unignore_doc_key(doc_repo, doc_path))
        if not removed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "página não estava fora da busca")
        return {"ignored": False}

    @app.get("/api/v1/relations/origins", tags=["relações"],
             summary="Origens de relação: rótulo, ordem, se é contestável ou gravada por pessoa/agent")
    async def relation_origins(_: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return origins_payload()

    @app.get("/api/v1/relations", tags=["relações"], summary="Tudo ligado a uma entidade (repo, doc:, op:, code:)")
    async def relations_of(request: Request, entity: str = Query(..., min_length=1, max_length=1500),
                           type: str | None = None, limit: int = Query(200, ge=1, le=1000),
                           _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        try:
            return await get_service(request).related(entity, type_=type, limit=limit)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.post("/api/v1/relations/rebuild", tags=["relações"],
              summary="Recalcula documentos e relações derivadas (sem custo de API)")
    async def relations_rebuild(request: Request, principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
        return await audited(request, principal, "relations:rebuild", None, get_service(request).rebuild_relations())

    @app.get("/api/v1/docs/suggestions", tags=["docs"],
             summary="Vínculos página → repo sugeridos por LLM (para revisar)")
    async def doc_suggestions(request: Request, status_: str = Query("suggested", alias="status"),
                              source: str | None = None, confidence: str | None = None,
                              action: str | None = None, limit: int = Query(200, ge=1, le=2000),
                              _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        try:
            return await get_service(request).doc_suggestions(status=status_, source=source, confidence=confidence,
                                                              action=action, limit=limit)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.post("/api/v1/docs/suggestions/review", tags=["docs"],
              summary="Aprova ou rejeita uma sugestão de vínculo (admin)")
    async def review_doc_suggestion(body: SuggestionReviewIn, request: Request,
                                    principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
        ok = await audited(request, principal, "docs:suggestion", body.model_dump(),
                           get_service(request).review_doc_suggestion(body.doc_repo, body.doc_path, body.repo,
                                                                      approve=body.approve))
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sugestão não encontrada (ou já revisada)")
        return {"ok": True}

    @app.get("/api/v1/docs/overview", tags=["docs"],
             summary="Páginas de documentação indexadas, com os repos ligados a cada uma")
    async def docs_overview(request: Request, limit: int = Query(500, ge=1, le=2000),
                            offset: int = Query(0, ge=0), docs_only: bool = False,
                            _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).docs_overview(limit=limit, offset=offset, docs_only=docs_only)

    @app.get("/api/v1/xray", tags=["repos"], summary="Inventário e vínculos para o Raio X")
    async def xray(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).xray()

    @app.get("/api/v1/repos/{name}/skipped", tags=["repos"],
             summary="Arquivos descartados na indexação de um repo, com o motivo")
    async def repo_skipped(name: str, request: Request, limit: int = Query(200, ge=1, le=2000),
                           _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).skipped_files(name, limit=limit)

    @app.get("/api/v1/repos/{name}/docs", tags=["docs"], summary="Páginas de documentação ligadas a um repo")
    async def repo_docs(name: str, request: Request, limit: int = Query(100, ge=1, le=500),
                        _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).linked_docs(name, limit=limit)

    @app.post("/api/v1/docs/links", tags=["docs"], status_code=201,
              summary="Grava vínculo manual ou de agent entre uma página e um repo (admin)")
    async def add_doc_link(body: DocLinkIn, request: Request,
                           principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        origin = "agent" if principal.kind == "agent" else "manual"
        return await audited(request, principal, "docs:link", body.model_dump(), get_service(request).link_doc_key(
            body.doc_repo, body.doc_path, body.repo, reason=body.reason, origin=origin,
        ))

    @app.delete("/api/v1/docs/links", tags=["docs"], summary="Remove um vínculo gravado (admin)")
    async def remove_doc_link(request: Request, doc_repo: str = Query(..., max_length=200),
                              doc_path: str = Query(..., max_length=1000), repo: str = Query(..., max_length=200),
                              principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
        params = {"doc_repo": doc_repo, "doc_path": doc_path, "repo": repo}
        removed = await audited(request, principal, "docs:unlink", params,
                                get_service(request).unlink_doc_key(doc_repo, doc_path, repo))
        if not removed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "vínculo não encontrado")
        return {"removed": True}

    @app.post("/api/v1/repos", tags=["repos"], status_code=201)
    async def add_repo(body: RepoIn, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        repo = await audited(request, principal, "repo:add", body.model_dump(), get_service(request).add_repo(
            body.source, name=body.name, branch=body.branch, include=body.include, extra_exclude=body.extra_exclude,
        ))
        return {"name": repo.name, "url": repo.url, "path": repo.path, "branch": repo.branch}

    @app.delete("/api/v1/repos/{name}", tags=["repos"])
    async def remove_repo(name: str, request: Request, keep_clone: bool = False,
                          principal: Principal = Depends(require(ADMIN))) -> dict[str, str]:
        result = await audited(request, principal, "repo:remove", {"name": name, "keep_clone": keep_clone},
                               get_service(request).remove_repo(name, keep_clone=keep_clone))
        return {"name": name, "result": result}

    @app.post("/api/v1/repos/{name}/unignore", tags=["repos"])
    async def unignore_repo(name: str, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, str]:
        await audited(request, principal, "repo:unignore", {"name": name}, get_service(request).unignore_repo(name))
        return {"name": name, "result": "pending"}

    # ---------------------------------------------------------- orgs

    @app.get("/api/v1/orgs", tags=["orgs"])
    async def list_orgs(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).org_summary()

    @app.post("/api/v1/orgs", tags=["orgs"], status_code=201)
    async def add_org(body: OrgIn, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        params = body.model_dump()
        name = params.pop("name")
        return await audited(request, principal, "org:add", body.model_dump(), get_service(request).add_org(name, **params))

    @app.patch("/api/v1/orgs/{name}", tags=["orgs"])
    async def set_org(name: str, body: OrgPatch, request: Request,
                      principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        return await audited(request, principal, "org:set", {"name": name, **body.model_dump()},
                             get_service(request).set_org(name, **body.model_dump()))

    @app.delete("/api/v1/orgs/{name}", tags=["orgs"])
    async def remove_org(name: str, request: Request, keep_clones: bool = False,
                         principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        n = await audited(request, principal, "org:remove", {"name": name, "keep_clones": keep_clones},
                          get_service(request).remove_org(name, keep_clones=keep_clones))
        return {"name": name, "removed_repos": n}

    @app.post("/api/v1/orgs/refresh", tags=["orgs"])
    async def refresh_orgs(request: Request, name: str | None = None,
                           principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        return await audited(request, principal, "org:refresh", {"name": name}, get_service(request).refresh_orgs(name))

    @app.get("/api/v1/openapi/catalog", tags=["openapi"])
    async def openapi_catalog(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).openapi_catalog()

    @app.get("/api/v1/openapi/status", tags=["openapi"])
    async def openapi_status(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).openapi_status()

    @app.post("/api/v1/openapi/preview", tags=["openapi"])
    async def openapi_preview(body: OpenApiSelection, request: Request,
                              _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        try:
            return await get_service(request).openapi_preview(body.domain, body.services)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/v1/openapi/index", tags=["openapi"], status_code=202)
    async def openapi_index(body: OpenApiSelection, request: Request,
                            principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        svc = get_service(request)
        enforce_rate_limit(request.app.state.limiter, settings, principal, "sync")
        try:
            job = await svc.start_sync(principal, None, batch=1, do_discover=False,
                                       openapi_domain=body.domain, openapi_services=body.services)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        await svc.audit(principal, "openapi:index", params={"job": job.id, **body.model_dump()})
        return {"job_id": job.id, "status": job.status, "url": f"/api/v1/sync/jobs/{job.id}"}

    # ---------------------------------------------------------- fila de enriquecimento (agents em outra máquina)

    @app.post("/api/v1/enrich/next", tags=["enrich"],
              summary="Reserva a próxima tarefa de enriquecimento (null = nada pendente)")
    async def enrich_next(body: EnrichNextIn, request: Request,
                          principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any] | None:
        """Devolve `id`, `text` (instruções + dados para o agent) e metadados. A tarefa fica reservada até o
        lease expirar; entregue com `POST /api/v1/enrich/tasks/{id}/submit`."""
        setattr(request.state, STATE_PARAMS, body.model_dump())
        try:
            return await get_service(request).enrich_next(agent=principal.actor[:100], repo=body.repo,
                                                          kind=body.kind, scope=body.scope)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    @app.post("/api/v1/enrich/tasks/{task_id}/submit", tags=["enrich"],
              summary="Entrega uma tarefa: o corpo é o JSON que o agent produziu (texto puro)")
    async def enrich_submit(task_id: str, request: Request,
                            principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any]:
        """Entrega recusada responde 400 com os erros legíveis; a tarefa continua reservada para nova tentativa."""
        raw = await request.body()
        if len(raw) > 512_000:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "entrega grande demais")
        payload = raw.decode("utf-8", errors="replace")
        return await audited(request, principal, "enrich:submit", {"task": task_id},
                             get_service(request).enrich_submit(task_id, payload, agent=principal.actor[:100]))

    @app.post("/api/v1/enrich/tasks/{task_id}/release", tags=["enrich"], summary="Devolve uma tarefa reservada à fila")
    async def enrich_release(task_id: str, request: Request,
                             principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, bool]:
        released = await audited(request, principal, "enrich:release", {"task": task_id},
                                 get_service(request).enrich_release(task_id))
        return {"released": bool(released)}

    @app.get("/api/v1/enrich/status", tags=["enrich"], summary="Progresso do enriquecimento: cartões, vínculos e resumos")
    async def enrich_status(request: Request, _: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any]:
        return await get_service(request).enrich_status()

    # ---------------------------------------------------------- sync

    @app.post("/api/v1/sync", tags=["sync"], status_code=202,
              summary="Indexa repos (job em background; dry_run responde na hora)")
    async def sync(body: SyncIn, request: Request,
                   principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
        svc = get_service(request)
        if body.dry_run:  # prévia: sem custo, só o balde de leitura (a interface a chama a cada mudança de lote)
            res = await audited(request, principal, "sync:dry-run", body.model_dump(), svc.sync(
                body.repos or None, batch="default" if body.batch is None else body.batch,
                do_discover=body.discover, dry_run=True, progress=False, allow_register=False,
            ))
            return res.to_dict()
        # Só iniciar uma indexação (custo na Voyage) gasta o balde de sync, que é de poucas por hora.
        enforce_rate_limit(request.app.state.limiter, settings, principal, "sync")
        try:
            job = await svc.start_sync(
                principal, body.repos or None,
                batch=settings.default_batch if body.batch is None else body.batch,
                do_discover=body.discover, pull=body.pull,
            )
        except RuntimeError as e:
            await svc.audit(principal, "sync:start", params=body.model_dump(), status=409, client_ip=client_ip(request))
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
        await svc.audit(principal, "sync:start", params={"job": job.id, **body.model_dump()},
                        status=202, client_ip=client_ip(request))
        return {"job_id": job.id, "status": job.status, "url": f"/api/v1/sync/jobs/{job.id}"}

    @app.get("/api/v1/sync/jobs", tags=["sync"], summary="Jobs de sync recentes")
    async def sync_jobs(request: Request, limit: int = Query(50, ge=1, le=200),
                        _: Principal = Depends(require(INDEX_WRITE))) -> list[dict[str, Any]]:
        return [j.to_dict() for j in await get_service(request).list_jobs(limit)]

    @app.get("/api/v1/sync/jobs/{job_id}", tags=["sync"])
    async def sync_job(job_id: str, request: Request, _: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
        job = await get_service(request).get_job(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job não encontrado")
        return job.to_dict()

    # ---------------------------------------------------------- tokens / auditoria

    @app.get("/api/v1/tokens", tags=["tokens"], summary="Seus tokens (admin vê todos)")
    async def list_tokens(request: Request, include_inactive: bool = False,
                          principal: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        tokens = await get_service(request).list_tokens(principal, include_inactive=include_inactive)
        return [t.to_dict() for t in tokens]

    @app.post("/api/v1/tokens", tags=["tokens"], status_code=201,
              summary="Emite token de agent (só com login no navegador; valor exibido UMA vez)")
    async def create_token(body: TokenIn, request: Request,
                           principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        token, info = await audited(request, principal, "token:create", body.model_dump(),
                                    get_service(request).create_agent_token(
                                        principal, name=body.name, scopes=body.scopes, ttl_days=body.ttl_days))
        return {"token": token, **info.to_dict(),
                "aviso": "guarde o token agora; ele não será mostrado de novo"}

    @app.delete("/api/v1/tokens/{token_id}", tags=["tokens"])
    async def revoke_token(token_id: str, request: Request,
                           principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, str]:
        await audited(request, principal, "token:revoke", {"id": token_id},
                      get_service(request).revoke_token(principal, token_id))
        return {"id": token_id, "result": "revoked"}

    # ---------------------------------------------------------- uso e usuários

    @app.get("/api/v1/usage", tags=["uso"], summary="Dashboard de uso (admin): totais, por tool, cliente e consultas")
    async def usage_report(request: Request, days: int = Query(30, ge=1, le=365), email: str | None = None,
                           _: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        try:
            return await get_service(request).usage_report(days, email=email)
        except ValueError as e:
            raise _http_error(e) from None

    @app.get("/api/v1/me/usage", tags=["uso"], summary="Meu uso (todas as minhas credenciais)")
    async def my_usage(request: Request, days: int = Query(30, ge=1, le=365),
                       principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        try:
            return await get_service(request).my_usage(principal, days)
        except ValueError as e:
            raise _http_error(e) from None

    @app.get("/api/v1/users", tags=["uso"], summary="Usuários: papel, estado e uso no período (admin)")
    async def list_users(request: Request, days: int = Query(30, ge=1, le=365),
                         _: Principal = Depends(require(ADMIN))) -> list[dict[str, Any]]:
        return await get_service(request).users_overview(days)

    @app.post("/api/v1/users/{email}/disable", tags=["uso"],
              summary="Desativa o usuário: nenhuma credencial dele vale (admin, login no navegador)")
    async def disable_user(email: str, request: Request,
                           principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        result: dict[str, Any] = await audited(request, principal, "user:disable", {"email": email},
                                               get_service(request).set_user_disabled(principal, email, True))
        return result

    @app.post("/api/v1/users/{email}/enable", tags=["uso"], summary="Reativa o usuário (admin, login no navegador)")
    async def enable_user(email: str, request: Request,
                          principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
        result: dict[str, Any] = await audited(request, principal, "user:enable", {"email": email},
                                               get_service(request).set_user_disabled(principal, email, False))
        return result

    @app.get("/api/v1/audit", tags=["auditoria"])
    async def audit_log(request: Request, limit: int = Query(100, ge=1, le=1000), actor: str | None = None,
                        _: Principal = Depends(require(ADMIN))) -> list[dict[str, Any]]:
        return await get_service(request).recent_audit(limit=limit, actor=actor)

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
