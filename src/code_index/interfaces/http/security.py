"""Autenticação/autorização das requisições HTTP.

- Credencial: `Authorization: Bearer cix_...` (agents) ou cookie de sessão `cix_session`
  (pessoas logadas pelo navegador: Google ou Microsoft). Token na URL nunca é aceito.
- Cookie + método que altera estado exige `Origin`/`Referer` da própria aplicação (CSRF).
- Rate limit por principal e categoria, compartilhado entre réplicas (Postgres, janela fixa; fail-open).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from ...domain.auth import Principal
from ...services import CodeIndexService
from ...settings import Settings

SESSION_COOKIE = "cix_session"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RateLimiter:
    """Rate limit compartilhado entre as réplicas da API: a contagem fica no Postgres (janela fixa por balde,
    chave e início da janela; ver services/rate_limit.py). Uma ida ao banco por requisição.

    Fail-open: se o banco falhar, ou antes de o serviço subir (lifespan), a requisição passa e a falha vai para o
    log. O rate limit protege custo e abuso; ele não deve derrubar a API quando o Postgres oscila.
    `key` é o id do token, o subject da sessão ou o IP: nunca o valor de um token.
    """

    def __init__(self, service: Callable[[], CodeIndexService]) -> None:
        self._service = service

    async def hit(self, bucket: str, key: str, *, limit: int, window: float) -> float | None:
        """Registra um evento. Retorna None se permitido, ou os segundos até liberar."""
        try:
            svc = self._service()
        except RuntimeError:  # serviço ainda não iniciado
            return None
        return await svc.rate_limit_hit(bucket, key, limit=limit, window=window)


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_service(request: Request) -> CodeIndexService:
    svc: CodeIndexService = request.app.state.service
    return svc


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def check_csrf(request: Request, settings: Settings) -> None:
    """Requisições autenticadas por cookie que mudam estado precisam vir da nossa origem."""
    if request.method not in _UNSAFE_METHODS:
        return
    expected = origin_of(settings.public_url)
    origin = request.headers.get("origin") or (
        origin_of(request.headers["referer"]) if request.headers.get("referer") else None
    )
    if origin is None or origin.lower() != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "origem da requisição não permitida")


async def optional_principal(
    request: Request,
    svc: CodeIndexService = Depends(get_service),
    settings: Settings = Depends(get_settings_dep),
) -> Principal | None:
    token = _bearer(request)
    via_cookie = False
    if token is None:
        token = request.cookies.get(SESSION_COOKIE)
        via_cookie = token is not None
    if token is None:
        return None
    principal = await svc.authenticate(token)
    if principal is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "token inválido, expirado ou revogado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if via_cookie:
        check_csrf(request, settings)
    request.state.principal = principal
    return principal


async def current_principal(principal: Principal | None = Depends(optional_principal)) -> Principal:
    if principal is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "autenticação necessária (Bearer token ou login em /auth/login)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require(scope: str, *, bucket: str = "read") -> Callable[..., Awaitable[Principal]]:
    """Dependência: exige `scope` e aplica o rate limit da categoria `bucket` (read|search|sync)."""

    async def dep(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"scope necessário: {scope}")
        await enforce_rate_limit(request.app.state.limiter, request.app.state.settings, principal, bucket)
        return principal

    return dep


async def enforce_rate_limit(limiter: RateLimiter, settings: Settings, principal: Principal, bucket: str) -> None:
    """Aplica o limite da categoria `bucket` (CODE_INDEX_RATE_*) ao principal; estourou: 429 com Retry-After."""
    limits = {
        "read": (settings.rate_limit_read_per_min, 60.0),
        "search": (settings.rate_limit_search_per_min, 60.0),
        "sync": (settings.rate_limit_sync_per_hour, 3600.0),
    }
    limit, window = limits[bucket]
    key = principal.token_id or principal.subject
    retry = await limiter.hit(bucket, key, limit=limit, window=window)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"limite de requisições ({bucket}) atingido",
            headers={"Retry-After": str(int(retry) + 1)},
        )
