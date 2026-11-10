"""Dashboard de uso (admin) e o uso da própria pessoa."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import http_error
from ..security import get_service, require

router = APIRouter()


@router.get("/api/v1/usage", tags=["uso"], summary="Dashboard de uso (admin): totais, por tool, cliente e consultas")
async def usage_report(request: Request, days: int = Query(30, ge=1, le=365), email: str | None = None,
                       _: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    try:
        return await get_service(request).usage_report(days, email=email)
    except ValueError as e:
        raise http_error(e) from None


@router.get("/api/v1/me/usage", tags=["uso"], summary="Meu uso (todas as minhas credenciais)")
async def my_usage(request: Request, days: int = Query(30, ge=1, le=365),
                   principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    try:
        return await get_service(request).my_usage(principal, days)
    except ValueError as e:
        raise http_error(e) from None
