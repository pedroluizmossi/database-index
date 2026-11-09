"""Autenticação/autorização das requisições HTTP.

- Credencial: `Authorization: Bearer cix_...` (agents) ou cookie de sessão `cix_session`
  (pessoas logadas pelo navegador: Google ou Microsoft). Token na URL nunca é aceito.
- Cookie + método que altera estado exige `Origin`/`Referer` da própria aplicação (CSRF).
- Rate limit em memória por principal (janela deslizante), por categoria.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from ...domain.auth import Principal
from ...services import CodeIndexService
from ...settings import Settings

SESSION_COOKIE = "cix_session"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RateLimiter:
    """Janela deslizante em memória: no máximo `limit` eventos por `window` segundos, por chave.

    Suficiente para uma instância. Com várias réplicas, troque por Redis/Postgres.
    """

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def hit(self, bucket: str, key: str, *, limit: int, window: float) -> float | None:
        """Registra um evento. Retorna None se permitido, ou os segundos até liberar."""
        now = time.monotonic()
        q = self._events[(bucket, key)]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return max(0.0, q[0] + window - now)
        q.append(now)
        return None


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
        enforce_rate_limit(request.app.state.limiter, request.app.state.settings, principal, bucket)
        return principal

    return dep


def enforce_rate_limit(limiter: RateLimiter, settings: Settings, principal: Principal, bucket: str) -> None:
    limits = {
        "read": (settings.rate_limit_read_per_min, 60.0),
        "search": (settings.rate_limit_search_per_min, 60.0),
        "sync": (settings.rate_limit_sync_per_hour, 3600.0),
    }
    limit, window = limits[bucket]
    key = principal.token_id or principal.subject
    retry = limiter.hit(bucket, key, limit=limit, window=window)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"limite de requisições ({bucket}) atingido",
            headers={"Retry-After": str(int(retry) + 1)},
        )
