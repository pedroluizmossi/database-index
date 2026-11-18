"""Fila de enriquecimento (agents em outra máquina): reservar, entregar e devolver tarefas; progresso."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import ENRICH_WRITE, INDEX_WRITE, Principal
from ..deps import audited
from ..security import get_service, require
from ..usage import STATE_PARAMS

router = APIRouter()


class EnrichFactsIn(BaseModel):
    repos: list[str] | None = Field(None, max_length=500, description="só estes repos (padrão: todos)")
    force: bool = Field(False, description="recoleta mesmo sem mudança de revisão")


class EnrichNextIn(BaseModel):
    kind: Literal["card", "doc_links", "file_summaries"] = "card"
    repo: str | None = Field(None, max_length=300, description="só este repo (doc_links: a fonte de documentação)")
    scope: Literal["todas", "sem-vinculo", "varios"] = Field("todas", description="doc_links: quais páginas")


@router.post("/api/v1/enrich/next", tags=["enrich"],
             summary="Reserva a próxima tarefa de enriquecimento (null = nada pendente)")
async def enrich_next(body: EnrichNextIn, request: Request,
                      principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any] | None:
    """Devolve `id`, `text` (instruções + dados para o agent) e metadados. A tarefa fica reservada até o
    lease expirar; entregue com `POST /api/v1/enrich/tasks/{id}/submit`."""
    setattr(request.state, STATE_PARAMS, body.model_dump())
    try:
        return await get_service(request).enrich_next(agent=principal.actor[:100], repo=body.repo,
                                                      kind=body.kind, scope=body.scope)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.post("/api/v1/enrich/tasks/{task_id}/submit", tags=["enrich"],
             summary="Entrega uma tarefa: o corpo é o JSON que o agent produziu (texto puro)")
async def enrich_submit(task_id: str, request: Request,
                        principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any]:
    """Entrega recusada responde 400 com os erros legíveis; a tarefa continua reservada para nova tentativa."""
    raw = await request.body()
    if len(raw) > 512_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "entrega grande demais")
    payload = raw.decode("utf-8", errors="replace")
    return await audited(request, principal, "enrich:submit", {"task": task_id},
                         get_service(request).enrich_submit(task_id, payload, agent=principal.actor[:100]))


@router.post("/api/v1/enrich/tasks/{task_id}/release", tags=["enrich"], summary="Devolve uma tarefa reservada à fila")
async def enrich_release(task_id: str, request: Request,
                         principal: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, bool]:
    released = await audited(request, principal, "enrich:release", {"task": task_id},
                             get_service(request).enrich_release(task_id))
    return {"released": bool(released)}


@router.post("/api/v1/enrich/facts", tags=["enrich"],
             summary="Recoleta os fatos dos repos (catalog-info, contratos dos perfis...) dos clones, sem LLM e sem custo")
async def enrich_facts(request: Request, body: EnrichFactsIn | None = Body(None),
                       principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, int]:
    """Mesmo efeito de `code-index enrich facts`: {repos, updated}. O sync já faz isso para os repos que mudam."""
    body = body or EnrichFactsIn()
    result: dict[str, int] = await audited(request, principal, "enrich:facts", body.model_dump(),
                                           get_service(request).enrich_facts(body.repos or None, force=body.force))
    return result


@router.post("/api/v1/enrich/apply-summaries", tags=["enrich"],
             summary="Reaplica os resumos de arquivos às colunas de busca e apaga os órfãos (sem custo)")
async def enrich_apply_summaries(request: Request,
                                 principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, int]:
    """Mesmo efeito de `code-index enrich apply-summaries`: {chunks, pruned, stale}."""
    result: dict[str, int] = await audited(request, principal, "enrich:apply-summaries", None,
                                           get_service(request).apply_file_summaries())
    return result


@router.get("/api/v1/enrich/status", tags=["enrich"], summary="Progresso do enriquecimento: cartões, vínculos e resumos")
async def enrich_status(request: Request, _: Principal = Depends(require(ENRICH_WRITE))) -> dict[str, Any]:
    return await get_service(request).enrich_status()
