"""Login no navegador (Google/Microsoft), logout e a parte com a pessoa do OAuth do MCP (login + consentimento)."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.auth.provider import construct_redirect_uri

from ....domain.auth import Principal
from ....infra.login import configured_providers, open_provider
from ....infra.oidc import LoginError
from ....settings import Settings
from ..deps import login_signer, oauth_signer
from ..mcp_oauth import load_pending
from ..pages import UI_DIR, chooser_page, consent_page, login_page, safe_next
from ..security import SESSION_COOKIE, check_csrf, client_ip, current_principal, get_service, get_settings_dep

router = APIRouter()

LOGIN_COOKIE = "cix_login"
LOGIN_MAX_AGE = 600
EXPIRED_REQUEST = ("Autorização expirou", "O pedido de acesso venceu ou foi alterado. Conecte o cliente MCP de novo.")


@router.get("/auth/providers", tags=["auth"], summary="Provedores de login ativos (Google, Microsoft)")
async def login_providers(settings: Settings = Depends(get_settings_dep)) -> list[dict[str, str]]:
    return [{"name": p.name, "label": p.label} for p in configured_providers(settings)]


@router.get("/auth/login", tags=["auth"], summary="Login no navegador (Google ou Microsoft; redireciona)")
async def login(provider: str = "", next_path: str = Query("", alias="next"),
                settings: Settings = Depends(get_settings_dep),
                signer: URLSafeTimedSerializer = Depends(login_signer)) -> Response:
    nxt = safe_next(next_path)
    if not provider:
        available = configured_providers(settings)
        if not available:
            return login_page(503, "Login indisponível", "Nenhum login configurado neste servidor: defina "
                              "GOOGLE_CLIENT_ID/SECRET e/ou AZURE_TENANT_ID/CLIENT_ID/SECRET.", retry=False)
        if len(available) > 1:
            return chooser_page(available, nxt)
        provider = available[0].name
    try:
        oidc = open_provider(settings, provider)
    except RuntimeError as e:
        return login_page(503, "Login indisponível", str(e), retry=False)
    start = oidc.start()
    resp = RedirectResponse(start.url, status_code=302)
    resp.set_cookie(
        LOGIN_COOKIE,
        signer.dumps({"s": start.state, "n": start.nonce, "v": start.code_verifier, "p": oidc.name, "x": nxt}),
        max_age=LOGIN_MAX_AGE, httponly=True, secure=settings.secure_cookies, samesite="lax", path="/auth",
    )
    return resp


@router.get("/auth/callback", tags=["auth"], include_in_schema=False)
async def callback(request: Request, code: str = "", state: str = "", error: str = "",
                   settings: Settings = Depends(get_settings_dep),
                   signer: URLSafeTimedSerializer = Depends(login_signer)) -> Response:
    svc = get_service(request)
    if error:
        return login_page(400, "Login cancelado", f"O provedor de login não concluiu o login ({error}).")
    try:
        data = signer.loads(request.cookies.get(LOGIN_COOKIE, ""), max_age=LOGIN_MAX_AGE)
    except BadSignature:
        return login_page(400, "Login expirou", "A sessão de login venceu ou não foi iniciada aqui.")
    if not code or not secrets.compare_digest(state.encode(), str(data["s"]).encode()):
        return login_page(400, "Login recusado", "A resposta do provedor não corresponde a este login.")
    try:
        oidc = open_provider(settings, data.get("p", "google"))
        identity = await oidc.finish(code=code, code_verifier=data["v"], nonce=data["n"])
    except RuntimeError as e:
        return login_page(503, "Login indisponível", str(e), retry=False)
    except LoginError as e:
        await svc.audit(None, "auth:login", params={"provider": data.get("p"), "error": str(e)}, status=403,
                        client_ip=client_ip(request))
        return login_page(403, "Acesso negado", str(e), retry=False)
    try:
        token, info = await svc.start_session(identity.email, identity.provider, display_name=identity.name)
    except PermissionError as e:  # usuário desativado por um admin
        await svc.audit(None, "auth:login", params={"email": identity.email, "error": str(e)}, status=403,
                        client_ip=client_ip(request))
        return login_page(403, "Acesso negado", str(e), retry=False)
    await svc.audit(None, "auth:login",
                    params={"email": identity.email, "provider": identity.provider, "scopes": list(info.scopes)},
                    status=200, client_ip=client_ip(request))
    # Volta para o consentimento do OAuth (se o login veio de lá), para a interface web se ela estiver
    # construída ou, senão, para a documentação da API.
    target = safe_next(data.get("x")) or ("/ui/" if UI_DIR.is_dir() else "/docs")
    resp = RedirectResponse(target, status_code=302)
    resp.delete_cookie(LOGIN_COOKIE, path="/auth")
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=settings.session_ttl_hours * 3600, httponly=True,
        secure=settings.secure_cookies, samesite="lax", path="/",
    )
    return resp


# ---------------------------------------------------------- OAuth do MCP: login + consentimento


async def _session_principal(request: Request) -> Principal | None:
    """Pessoa logada no navegador (só o cookie de sessão vale aqui, nunca um Bearer)."""
    token = request.cookies.get(SESSION_COOKIE)
    principal = await get_service(request).authenticate(token) if token else None
    return principal if principal is not None and principal.kind == "session" else None


def _to_login(req: str) -> RedirectResponse:
    return RedirectResponse("/auth/login?" + urlencode({"next": f"/auth/authorize?req={req}"}), status_code=303)


@router.get("/auth/authorize", tags=["auth"], include_in_schema=False)
async def oauth_consent(request: Request, req: str = "",
                        signer: URLSafeTimedSerializer = Depends(oauth_signer)) -> Response:
    pending = load_pending(signer, req)
    if pending is None:
        return login_page(400, *EXPIRED_REQUEST, retry=False)
    principal = await _session_principal(request)
    if principal is None:
        return _to_login(req)
    client = await get_service(request).get_oauth_client(pending["client_id"])
    if client is None:
        return login_page(400, *EXPIRED_REQUEST, retry=False)
    name = str(client[0].get("client_name") or "Um cliente MCP")
    return consent_page(client_name=name, redirect_uri=pending["redirect_uri"], email=principal.subject, req=req)


@router.post("/auth/authorize", tags=["auth"], include_in_schema=False)
async def oauth_decide(request: Request, settings: Settings = Depends(get_settings_dep),
                       signer: URLSafeTimedSerializer = Depends(oauth_signer)) -> Response:
    check_csrf(request, settings)  # formulário da nossa própria origem
    form = await request.form()
    req, decision = str(form.get("req", "")), str(form.get("decision", ""))
    pending = load_pending(signer, req)
    if pending is None:
        return login_page(400, *EXPIRED_REQUEST, retry=False)
    principal = await _session_principal(request)
    if principal is None:
        return _to_login(req)
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
        return login_page(403, "Acesso negado", str(e), retry=False)
    await svc.audit(principal, "oauth:authorize", params={"client_id": client_id, "decision": "allow"},
                    status=200, client_ip=client_ip(request))
    return RedirectResponse(construct_redirect_uri(redirect_uri, code=code, state=state), status_code=303)


@router.post("/auth/logout", tags=["auth"], summary="Encerra a sessão")
async def logout(request: Request, principal: Principal = Depends(current_principal)) -> Response:
    svc = get_service(request)
    await svc.end_session(principal)
    await svc.audit(principal, "auth:logout", client_ip=client_ip(request))
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
