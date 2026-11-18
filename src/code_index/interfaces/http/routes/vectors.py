"""Vetores extras da busca (resumos e perguntas dos arquivos, cartões dos repos): estado e geração sob pedido."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ....domain.auth import INDEX_WRITE, Principal
from ..deps import audited
from ..security import client_ip, enforce_rate_limit, get_service, get_settings_dep, require

router = APIRouter()


class EmbedIn(BaseModel):
    kinds: list[Literal["question", "summary", "repo_card"]] | None = Field(
        None, description="só estes tipos (padrão: todos os pendentes)")
    limit: int | None = Field(None, ge=1, le=1_000_000, description="no máximo N textos nesta rodada")
    dry_run: bool = Field(False, description="só conta o que seria gerado e apagado (sem custo; responde 200 na hora)")


@router.get("/api/v1/vectors/status", tags=["sync"],
            summary="Vetores de resumos e cartões: em dia, pendentes (custam ao gerar) e obsoletos")
async def vector_status(request: Request, _: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    return await get_service(request).vector_status()


@router.post("/api/v1/vectors/embed", tags=["sync"], status_code=202,
             summary="Gera os vetores pendentes em segundo plano (custo de embedding na Voyage); dry_run só conta")
async def vector_embed(request: Request, response: Response, body: EmbedIn | None = Body(None),
                       principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    svc = get_service(request)
    body = body or EmbedIn()
    kinds = [str(k) for k in body.kinds] if body.kinds else None
    if body.dry_run:  # o mesmo cálculo do `code-index vectors embed --dry-run`: sem custo, não gasta o balde de sync
        response.status_code = status.HTTP_200_OK
        result: dict[str, Any] = await audited(
            request, principal, "vectors:dry-run", {"kinds": kinds, "limit": body.limit},
            svc.embed_vectors(dry_run=True, limit=body.limit, kinds=kinds))
        return result
    await enforce_rate_limit(request.app.state.limiter, get_settings_dep(request), principal, "sync")
    try:
        state = await svc.start_vector_embedding(principal, kinds=kinds, limit=body.limit)
    except RuntimeError as e:
        await svc.audit(principal, "vectors:start", status=409, client_ip=client_ip(request))
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    await svc.audit(principal, "vectors:start",
                    params={"pending": state["pending"], "kinds": body.kinds, "limit": body.limit}, status=202,
                    client_ip=client_ip(request))
    return state
