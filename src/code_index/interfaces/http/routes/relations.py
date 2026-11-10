"""Relações entre entidades (repo, doc:, op:, code:): origens, consulta e recálculo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ....domain.auth import INDEX_WRITE, SEARCH_READ, Principal
from ....domain.relations import origins_payload
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


@router.get("/api/v1/relations/origins", tags=["relações"],
            summary="Origens de relação: rótulo, ordem, se é contestável ou gravada por pessoa/agent")
async def relation_origins(_: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return origins_payload()


@router.get("/api/v1/relations", tags=["relações"], summary="Tudo ligado a uma entidade (repo, doc:, op:, code:)")
async def relations_of(request: Request, entity: str = Query(..., min_length=1, max_length=1500),
                       type: str | None = None, limit: int = Query(200, ge=1, le=1000),
                       _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    try:
        return await get_service(request).related(entity, type_=type, limit=limit)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.post("/api/v1/relations/rebuild", tags=["relações"],
             summary="Recalcula documentos e relações derivadas (sem custo de API)")
async def relations_rebuild(request: Request, principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    return await audited(request, principal, "relations:rebuild", None, get_service(request).rebuild_relations())
