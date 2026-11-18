"""Grafo de chamadas e leituras dos clones (grafo e casos de teste): consultas e releitura sob pedido."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status

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
             summary="Relê o grafo de chamadas ou os casos de teste (sem custo de API): em segundo plano, ou "
                     "esperando o fim com wait=true")
async def start_scan(kind: Literal["graph", "tests"], request: Request, response: Response,
                     force: bool = Query(False), repo: list[str] | None = Query(None),
                     wait: bool = Query(False, description="espera a leitura e devolve {kind, result} (como a CLI)"),
                     principal: Principal = Depends(require(INDEX_WRITE))) -> dict[str, Any]:
    """Sem `wait`, responde 202 com o estado (acompanhe em GET /api/v1/scans/status; o estado é do processo que
    recebeu o pedido). Com `wait`, responde 200 com o mesmo resultado de `code-index graph|tests scan`."""
    svc = get_service(request)
    await enforce_rate_limit(request.app.state.limiter, get_settings_dep(request), principal, "sync")
    repos = repo or None
    params = {"force": force, "repos": repos}
    if wait:
        response.status_code = status.HTTP_200_OK
        scan = svc.run_scan(kind, principal, force=force, repos=repos)  # mesma guarda do segundo plano (409)
        return {"kind": kind, "result": await audited(request, principal, f"scan:{kind}", params, scan)}
    try:
        state = await svc.start_scan(kind, principal, force=force, repos=repos)
    except (ValueError, RuntimeError) as e:
        err = http_error(e)
        await svc.audit(principal, f"scan:{kind}:start", params=params, status=err.status_code,
                        client_ip=client_ip(request))
        raise err from None
    await svc.audit(principal, f"scan:{kind}:start", params=params, status=202, client_ip=client_ip(request))
    return state
