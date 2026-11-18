"""Sync: enfileirar uma indexação (job do worker) e acompanhar os jobs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import INDEX_WRITE, Principal
from ..deps import audited, http_error
from ..security import client_ip, enforce_rate_limit, get_service, get_settings_dep, require

router = APIRouter()


class SyncIn(BaseModel):
    repos: list[str] | None = Field(None, description="nomes a indexar; vazio = lote automático")
    batch: int | None = Field(None, ge=0, le=50, description="tamanho do lote (padrão do servidor)")
    all: bool = Field(False, description="sem limite de lote (ignora batch), como o --all da CLI")
    discover: bool = True
    pull: bool = True
    full: bool = Field(False, description="reprocessa tudo, ignorando caches (caro!; só admin)")
    pages: list[str] | None = Field(
        None, max_length=200,
        description="fonte de documentação: só estas páginas (sem idioma, ex.: Plataforma/Email); exige um único "
                    "item em repos (ex.: docs__wiki), como o --page da CLI")
    dry_run: bool = False


@router.post("/api/v1/sync", tags=["sync"], status_code=202,
             summary="Enfileira uma indexação (job executado pelo worker; dry_run responde na hora)")
async def sync(body: SyncIn, request: Request,
               principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    svc = get_service(request)
    settings = get_settings_dep(request)
    if body.dry_run:  # prévia: sem custo, só o balde de leitura (a interface a chama a cada mudança de lote)
        if body.pages:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "pages não combina com dry_run (use GET /api/v1/docs/{nome}/preview)")
        batch: int | None | Literal["default"] = None if body.all else (
            "default" if body.batch is None else body.batch)
        res = await audited(request, principal, "sync:dry-run", body.model_dump(), svc.sync(
            body.repos or None, batch=batch,
            do_discover=body.discover, full=body.full, dry_run=True, progress=False, allow_register=False,
        ))
        return res.to_dict()
    # Só iniciar uma indexação (custo na Voyage) gasta o balde de sync, que é de poucas por hora.
    await enforce_rate_limit(request.app.state.limiter, settings, principal, "sync")
    try:
        job = await svc.start_sync(
            principal, body.repos or None,
            batch=None if body.all else (settings.default_batch if body.batch is None else body.batch),
            do_discover=body.discover, pull=body.pull, full=body.full, doc_pages=body.pages,
        )
    except (ValueError, RuntimeError, PermissionError) as e:  # 400 pedido inválido, 403 full sem admin, 409 job ativo
        err = http_error(e)
        await svc.audit(principal, "sync:start", params=body.model_dump(), status=err.status_code,
                        client_ip=client_ip(request))
        raise err from None
    await svc.audit(principal, "sync:start", params={"job": job.id, **body.model_dump()},
                    status=202, client_ip=client_ip(request))
    return {"job_id": job.id, "status": job.status, "url": f"/api/v1/sync/jobs/{job.id}"}


@router.get("/api/v1/sync/jobs", tags=["sync"], summary="Jobs de sync recentes")
async def sync_jobs(request: Request, limit: int = Query(50, ge=1, le=200),
                    _: Principal = Depends(require(INDEX_WRITE))) -> list[dict[str, Any]]:
    return [j.to_dict() for j in await get_service(request).list_jobs(limit)]


@router.get("/api/v1/sync/jobs/{job_id}", tags=["sync"])
async def sync_job(job_id: str, request: Request, _: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    job = await get_service(request).get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job não encontrado")
    return job.to_dict()
