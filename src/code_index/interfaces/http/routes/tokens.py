"""Tokens de agent (emitidos só por pessoas logadas no navegador) e a trilha de auditoria."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = [SEARCH_READ]
    ttl_days: int | None = Field(None, ge=1, le=365)


@router.get("/api/v1/tokens", tags=["tokens"], summary="Seus tokens (admin vê todos)")
async def list_tokens(request: Request, include_inactive: bool = False,
                      principal: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    tokens = await get_service(request).list_tokens(principal, include_inactive=include_inactive)
    return [t.to_dict() for t in tokens]


@router.post("/api/v1/tokens", tags=["tokens"], status_code=201,
             summary="Emite token de agent (só com login no navegador; valor exibido UMA vez)")
async def create_token(body: TokenIn, request: Request,
                       principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    token, info = await audited(request, principal, "token:create", body.model_dump(),
                                get_service(request).create_agent_token(
                                    principal, name=body.name, scopes=body.scopes, ttl_days=body.ttl_days))
    return {"token": token, **info.to_dict(),
            "aviso": "guarde o token agora; ele não será mostrado de novo"}


@router.delete("/api/v1/tokens/{token_id}", tags=["tokens"])
async def revoke_token(token_id: str, request: Request,
                       principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, str]:
    await audited(request, principal, "token:revoke", {"id": token_id},
                  get_service(request).revoke_token(principal, token_id))
    return {"id": token_id, "result": "revoked"}


@router.get("/api/v1/audit", tags=["auditoria"])
async def audit_log(request: Request, limit: int = Query(100, ge=1, le=1000), actor: str | None = None,
                    _: Principal = Depends(require(ADMIN))) -> list[dict[str, Any]]:
    return await get_service(request).recent_audit(limit=limit, actor=actor)
