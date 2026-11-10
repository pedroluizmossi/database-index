"""Dono no DDD (domínio/serviço) de cada repo ou pasta de monorepo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class OwnerIn(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=130, description="'dominio' ou 'dominio/servico'")
    path: str | None = Field(None, max_length=500, description="pasta do repo (monorepo); só com um repo")
    create: bool = Field(False, description="cria o domínio/serviço se não existir no catálogo")
    reason: str = Field("", max_length=500)


class OwnerClearIn(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=500)
    path: str | None = Field(None, max_length=500)


@router.get("/api/v1/ownership/catalog", tags=["dono"],
            summary="Domínios e serviços (do SDL e criados à mão), repos por serviço e cobertura")
async def ownership_catalog(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).ownership_catalog()


@router.get("/api/v1/ownership", tags=["dono"],
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


@router.put("/api/v1/ownership", tags=["dono"], summary="Define o dono manual de repos ou de uma pasta (admin)")
async def set_owner(body: OwnerIn, request: Request,
                    principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await audited(request, principal, "owner:set:api", body.model_dump(exclude={"repos"}),
                         get_service(request).set_owner(principal, body.repos, body.owner, path=body.path,
                                                        create=body.create, reason=body.reason))


@router.post("/api/v1/ownership/clear", tags=["dono"],
             summary="Remove o dono manual (volta ao inferido) de repos ou de uma pasta (admin)")
async def clear_owner(body: OwnerClearIn, request: Request,
                      principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await audited(request, principal, "owner:clear:api", body.model_dump(exclude={"repos"}),
                         get_service(request).clear_owner(principal, body.repos, path=body.path))
