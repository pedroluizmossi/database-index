"""APIs da plataforma: onde uma operação é implementada e o catálogo/indexação das specs OpenAPI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..security import enforce_rate_limit, get_service, get_settings_dep, require

router = APIRouter()


class OpenApiSelection(BaseModel):
    domain: str = Field(min_length=1, max_length=100)
    services: list[str] | None = None


@router.get("/api/v1/api-impls", tags=["openapi"],
            summary="Onde uma operação da API é implementada (handler do .sdl; sem custo)")
async def api_impls(request: Request, operation: str = Query(..., min_length=1, max_length=300),
                    service: str | None = None, domain: str | None = None,
                    limit: int = Query(20, ge=1, le=100),
                    _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    try:
        return await get_service(request).find_api_impl(operation, service=service, domain=domain, limit=limit)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.get("/api/v1/openapi/catalog", tags=["openapi"])
async def openapi_catalog(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).openapi_catalog()


@router.get("/api/v1/openapi/status", tags=["openapi"])
async def openapi_status(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).openapi_status()


@router.post("/api/v1/openapi/preview", tags=["openapi"])
async def openapi_preview(body: OpenApiSelection, request: Request,
                          _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    try:
        return await get_service(request).openapi_preview(body.domain, body.services)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/v1/openapi/index", tags=["openapi"], status_code=202)
async def openapi_index(body: OpenApiSelection, request: Request,
                        principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    svc = get_service(request)
    enforce_rate_limit(request.app.state.limiter, get_settings_dep(request), principal, "sync")
    try:
        job = await svc.start_sync(principal, None, batch=1, do_discover=False,
                                   openapi_domain=body.domain, openapi_services=body.services)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await svc.audit(principal, "openapi:index", params={"job": job.id, **body.model_dump()})
    return {"job_id": job.id, "status": job.status, "url": f"/api/v1/sync/jobs/{job.id}"}
