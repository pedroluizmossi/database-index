"""Dependências comuns das rotas: tradução de erros do serviço para HTTP, auditoria e o estado do app.

Autenticação, scopes (`require`) e rate limit ficam em security.py. O que o `create_app` monta uma vez (serviço,
Settings, rate limiter, assinadores do login e do OAuth) fica em `request.app.state` e é lido daqui.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer

from ...domain.auth import Principal
from ...domain.usage import result_count
from .security import client_ip, get_service
from .usage import STATE_PARAMS, STATE_RESULT_COUNT


def http_error(e: Exception) -> HTTPException:
    if isinstance(e, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    if isinstance(e, RuntimeError):
        return HTTPException(status.HTTP_409_CONFLICT, str(e))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


async def audited(request: Request, principal: Principal | None, action: str, params: dict[str, Any] | None,
                  fn: Any) -> Any:
    """Executa `fn` (corrotina do serviço) e audita o resultado; erros de negócio viram 400/403/409."""
    svc = get_service(request)
    setattr(request.state, STATE_PARAMS, params)  # para o registro de uso (UsageMiddleware)
    try:
        result = await fn
    except (ValueError, RuntimeError, PermissionError) as e:
        err = http_error(e)
        await svc.audit(principal, action, params=params, status=err.status_code, client_ip=client_ip(request))
        raise err from None
    await svc.audit(principal, action, params=params, status=200, client_ip=client_ip(request))
    setattr(request.state, STATE_RESULT_COUNT, result_count(result))
    return result


def login_signer(request: Request) -> URLSafeTimedSerializer:
    """Assina o cookie do login no navegador (state, nonce, PKCE, provedor e destino)."""
    signer: URLSafeTimedSerializer = request.app.state.login_signer
    return signer


def oauth_signer(request: Request) -> URLSafeTimedSerializer:
    """Assina o pedido de autorização do OAuth do MCP (ver mcp_oauth.py)."""
    signer: URLSafeTimedSerializer = request.app.state.oauth_signer
    return signer
