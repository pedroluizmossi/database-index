"""Pessoas: quem sou eu, minhas configurações e a gestão de usuários (admin)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal, session_provider
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class UserSettingsIn(BaseModel):
    settings: dict[str, Any] = Field(description="chave → valor; null volta ao padrão", max_length=50)


@router.get("/api/v1/me", tags=["auth"], summary="Quem sou eu")
async def me(principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return {"subject": principal.subject, "kind": principal.kind, "scopes": sorted(principal.scopes),
            "token_name": principal.token_name, "provider": session_provider(principal)}


@router.get("/api/v1/me/settings", tags=["auth"], summary="Minhas configurações (valores, padrões e schema)")
async def my_settings(request: Request, principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).user_settings(principal)


@router.patch("/api/v1/me/settings", tags=["auth"],
              summary="Altera minhas configurações (só com login no navegador; null volta ao padrão)")
async def update_my_settings(body: UserSettingsIn, request: Request,
                             principal: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    result: dict[str, Any] = await audited(request, principal, "settings:update", {"keys": sorted(body.settings)},
                                           get_service(request).update_user_settings(principal, body.settings))
    return result


@router.get("/api/v1/users", tags=["uso"], summary="Usuários: papel, estado e uso no período (admin)")
async def list_users(request: Request, days: int = Query(30, ge=1, le=365),
                     _: Principal = Depends(require(ADMIN))) -> list[dict[str, Any]]:
    return await get_service(request).users_overview(days)


@router.post("/api/v1/users/{email}/disable", tags=["uso"],
             summary="Desativa o usuário: nenhuma credencial dele vale (admin, login no navegador)")
async def disable_user(email: str, request: Request,
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    result: dict[str, Any] = await audited(request, principal, "user:disable", {"email": email},
                                           get_service(request).set_user_disabled(principal, email, True))
    return result


@router.post("/api/v1/users/{email}/enable", tags=["uso"], summary="Reativa o usuário (admin, login no navegador)")
async def enable_user(email: str, request: Request,
                      principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    result: dict[str, Any] = await audited(request, principal, "user:enable", {"email": email},
                                           get_service(request).set_user_disabled(principal, email, False))
    return result
