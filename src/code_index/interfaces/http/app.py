"""API HTTP (REST em /api/v1, login Google em /auth, MCP em /mcp), num único processo.

Autorização por scope (ver domain/auth.py):
  search:read  GET repos/orgs, busca, leitura de arquivo, MCP
  index:write  sync (job em background)
  admin        gerenciar repos/orgs, auditoria; tokens de outros usuários
Tokens só são emitidos por pessoas logadas via Google (cookie de sessão).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ...domain.auth import ADMIN, INDEX_WRITE, SEARCH_READ, Principal
from ...infra.google_oidc import GoogleOIDC, LoginError
from ...services import CodeIndexService, open_service
from ...settings import Settings
from .mcp import build_mcp, transport_security
from .security import (
    SESSION_COOKIE,
    RateLimiter,
    client_ip,
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
    "a{color:#4f46e5;font-weight:500}"
    "@media (prefers-color-scheme:dark){body{background:#09090b;color:#fafafa}"
    "main{background:#18181b;border-color:#27272a}p{color:#a1a1aa}}"
)


def _login_page(status_code: int, title: str, message: str, *, retry: bool = True) -> HTMLResponse:
    """Página de erro do login. Todo texto passa por escape: nada vindo do Google vira HTML."""
    retry_link = '<a href="/auth/login">Tentar de novo</a> · ' if retry else ""
    body = (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_PAGE_CSS}</style></head><body><main>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p>"
        f'<p>{retry_link}<a href="/ui/">Voltar para a interface</a></p>'
        "</main></body></html>"
    )
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"},
    )
LOGIN_MAX_AGE = 600
UI_DIR = Path(__file__).resolve().parents[4] / "frontend" / "dist"  # build de `pnpm build` em frontend/


# ------------------------------------------------------------------ modelos de entrada


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


class SyncIn(BaseModel):
    repos: list[str] | None = Field(None, description="nomes a indexar; vazio = lote automático")
    batch: int | None = Field(None, ge=0, le=50, description="tamanho do lote (padrão do servidor)")
    discover: bool = True
    pull: bool = True
    dry_run: bool = False


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


class DocLinkIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
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


def create_app(settings: Settings) -> FastAPI:
    secret = settings.require_auth_secret()
    limiter = RateLimiter()
    holder: dict[str, CodeIndexService] = {}

    def service() -> CodeIndexService:
        if "svc" not in holder:
            raise RuntimeError("serviço ainda não iniciado")
        return holder["svc"]

    mcp = build_mcp(settings, service, limiter)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", transport_security=transport_security(settings), host=settings.http_host
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            svc = await stack.enter_async_context(open_service(settings))
            holder["svc"] = app.state.service = svc
            await svc.recover_interrupted_jobs()  # jobs órfãos de uma execução anterior do servidor
            await stack.enter_async_context(mcp.session_manager.run())
            try:
                yield
            finally:
                holder.pop("svc", None)  # open_service encerra os jobs em andamento

    app = FastAPI(
        title="code-index",
        version="1",
        description="Índice de código (CocoIndex + Voyage + pgvector). Autentique com Bearer token "
        "ou faça login em /auth/login.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.limiter = limiter

    hostnames = {urlsplit(settings.public_url).hostname or "localhost"}
    hostnames |= {h.rsplit(":", 1)[0] for h in settings.allowed_hosts}
    if settings.http_host in ("127.0.0.1", "localhost", "::1"):
        hostnames |= {"127.0.0.1", "localhost", "::1", "testserver"}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(hostnames))

    signer = URLSafeTimedSerializer(secret, salt="code-index-login")

    async def audited(request: Request, principal: Principal | None, action: str, params: dict[str, Any] | None,
                      fn: Any) -> Any:
        svc = get_service(request)
        try:
            result = await fn
        except (ValueError, RuntimeError, PermissionError) as e:
            err = _http_error(e)
            await svc.audit(principal, action, params=params, status=err.status_code, client_ip=client_ip(request))
            raise err from None
        await svc.audit(principal, action, params=params, status=200, client_ip=client_ip(request))
        return result

    # ---------------------------------------------------------- saúde / login

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/login", tags=["auth"], summary="Login com Google (redireciona)")
    async def login() -> Response:
        try:
            oidc = GoogleOIDC(settings)
        except RuntimeError as e:
            return _login_page(503, "Login indisponível", str(e), retry=False)
        start = oidc.start()
        resp = RedirectResponse(start.url, status_code=302)
        resp.set_cookie(
            LOGIN_COOKIE, signer.dumps({"s": start.state, "n": start.nonce, "v": start.code_verifier}),
            max_age=LOGIN_MAX_AGE, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/auth",
        )
        return resp

    @app.get("/auth/callback", tags=["auth"], include_in_schema=False)
    async def callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
        svc = get_service(request)
        if error:
            return _login_page(400, "Login cancelado", f"O Google não concluiu o login ({error}).")
        try:
            data = signer.loads(request.cookies.get(LOGIN_COOKIE, ""), max_age=LOGIN_MAX_AGE)
        except BadSignature:
            return _login_page(400, "Login expirou", "A sessão de login venceu ou não foi iniciada aqui.")
        if not code or not secrets.compare_digest(state, data["s"]):
            return _login_page(400, "Login recusado", "A resposta do Google não corresponde a este login.")
        try:
            identity = await GoogleOIDC(settings).finish(code=code, code_verifier=data["v"], nonce=data["n"])
        except LoginError as e:
            await svc.audit(None, "auth:login", params={"error": str(e)}, status=403, client_ip=client_ip(request))
            return _login_page(403, "Acesso negado", str(e), retry=False)
        token, info = await svc.start_session(identity.email)
        await svc.audit(None, "auth:login", params={"email": identity.email, "scopes": list(info.scopes)},
                        status=200, client_ip=client_ip(request))
        # Volta para a interface web se ela estiver construída; senão, para a documentação da API.
        resp = RedirectResponse("/ui/" if UI_DIR.is_dir() else "/docs", status_code=302)
        resp.delete_cookie(LOGIN_COOKIE, path="/auth")
        resp.set_cookie(
            SESSION_COOKIE, token, max_age=settings.session_ttl_hours * 3600, httponly=True,
            secure=settings.secure_cookies, samesite="lax", path="/",
        )
        return resp

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
                "token_name": principal.token_name}

    # ---------------------------------------------------------- consulta

    @app.post("/api/v1/search", tags=["busca"], summary="Busca híbrida + rerank")
    async def search(body: SearchIn, request: Request,
                     principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> list[dict[str, Any]]:
        svc = get_service(request)
        hits = await audited(request, principal, "search", body.model_dump(exclude={"rerank"}), svc.search(
            body.query, top_k=body.top_k, repos=body.repos, language=body.language, path_glob=body.path_glob,
            mode=body.mode, rerank=body.rerank, kind=body.kind, include_linked_docs=body.include_linked_docs,
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

    # ---------------------------------------------------------- repos

    @app.get("/api/v1/repos", tags=["repos"])
    async def list_repos(request: Request, status_: str | None = Query(None, alias="status"), org: str | None = None,
                         name: str | None = None, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        rows = await get_service(request).list_repos(status=status_, org=org, name=name)
        return [r.to_dict() for r in rows]

    @app.get("/api/v1/docs/{name}/preview", tags=["docs"],
             summary="Prévia das páginas de uma fonte de documentação (sem custo)")
    async def docs_preview(name: str, request: Request, limit: int = Query(200, ge=1, le=1000),
                           _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
        return await get_service(request).docs_preview(name, limit=limit)

    @app.get("/api/v1/docs/overview", tags=["docs"],
             summary="Páginas de documentação indexadas, com os repos ligados a cada uma")
    async def docs_overview(request: Request, limit: int = Query(500, ge=1, le=2000),
                            _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
        return await get_service(request).docs_overview(limit=limit)

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

    # ---------------------------------------------------------- sync

    @app.post("/api/v1/sync", tags=["sync"], status_code=202,
              summary="Indexa repos (job em background; dry_run responde na hora)")
    async def sync(body: SyncIn, request: Request,
                   principal: Principal = Depends(require(INDEX_WRITE, bucket="sync"))) -> dict[str, Any]:
        svc = get_service(request)
        if body.dry_run:
            res = await audited(request, principal, "sync:dry-run", body.model_dump(), svc.sync(
                body.repos or None, batch="default" if body.batch is None else body.batch,
                do_discover=body.discover, dry_run=True, progress=False, allow_register=False,
            ))
            return res.to_dict()
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
              summary="Emite token de agent (só via login Google; valor exibido UMA vez)")
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
