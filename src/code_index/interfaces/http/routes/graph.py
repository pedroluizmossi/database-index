"""Grafo de chamadas e leituras dos clones (grafo e casos de teste): consultas e releitura sob pedido."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ....domain.auth import INDEX_WRITE, SEARCH_READ, Principal
from ..deps import audited, http_error
from ..security import client_ip, enforce_rate_limit, get_service, get_settings_dep, require

router = APIRouter()


@router.get("/api/v1/calls", tags=["busca"],
            summary="Grafo de chamadas: quem chama (callers) ou o que é chamado por (callees) um símbolo")
async def call_graph(request: Request, target: str = Query(min_length=1, max_length=300),
                     direction: Literal["callers", "callees"] = Query("callers"),
                     repo: list[str] | None = Query(None), limit: int = Query(30, ge=1, le=200),
                     external: bool = Query(False, description="só chamadas entre arquivos diferentes"),
                     principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> dict[str, Any]:
    return await audited(request, principal, "calls", {"target": target, "direction": direction, "repo": repo},
                         get_service(request).call_graph(target, direction=direction, repos=repo, limit=limit,
                                                         external=external))


@router.get("/api/v1/calls/counts", tags=["busca"],
            summary="Chamadas entre um arquivo e os outros do repo: quantos chamadores e quantos alvos")
async def call_counts(request: Request, repo: str = Query(min_length=1, max_length=300),
                      path: str = Query(min_length=1, max_length=1000),
                      _: Principal = Depends(require(SEARCH_READ))) -> dict[str, int]:
    # sem auditoria: é chamada a cada arquivo aberto na interface (só leitura, sem conteúdo)
    try:
        return await get_service(request).call_counts(repo, path)
    except ValueError as e:
        raise http_error(e) from None


@router.get("/api/v1/repos/{repo}/central-files", tags=["repos"],
            summary="Arquivos mais chamados por outros arquivos do repo (por onde começar a ler)")
async def central_files(repo: str, request: Request, limit: int = Query(8, ge=1, le=50),
                        _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    try:
        return {"files": await get_service(request).central_files(repo, limit=limit)}
    except ValueError as e:
        raise http_error(e) from None


@router.get("/api/v1/scans/status", tags=["sync"],
            summary="Grafo de chamadas e casos de teste lidos dos clones: números e leitura em andamento")
async def scans_status(request: Request, _: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    return await get_service(request).scans_status()


@router.post("/api/v1/scans/{kind}", tags=["sync"], status_code=202,
             summary="Relê o grafo de chamadas ou os casos de teste em segundo plano (sem custo de API)")
async def start_scan(kind: Literal["graph", "tests"], request: Request, force: bool = Query(False),
                     principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    svc = get_service(request)
    enforce_rate_limit(request.app.state.limiter, get_settings_dep(request), principal, "sync")
    try:
        state = await svc.start_scan(kind, principal, force=force)
    except RuntimeError as e:
        await svc.audit(principal, f"scan:{kind}:start", status=409, client_ip=client_ip(request))
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    await svc.audit(principal, f"scan:{kind}:start", params={"force": force}, status=202, client_ip=client_ip(request))
    return state
