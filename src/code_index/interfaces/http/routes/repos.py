"""Repositórios: catálogo, cartão, arquivos indexados/descartados, Raio X e gestão (admin)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class RepoIn(BaseModel):
    source: str = Field(description="URL git, 'org/repo' do GitHub ou diretório local do servidor")
    name: str | None = None
    branch: str | None = None
    include: list[str] | None = None
    extra_exclude: list[str] | None = None


@router.get("/api/v1/repos/{name}/files", tags=["repos"],
            summary="Arquivos indexados de um repo, com o resumo de cada um (escrito por agent), se houver")
async def repo_files(name: str, request: Request,
                     _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).repo_files(name)


@router.get("/api/v1/repos", tags=["repos"])
async def list_repos(request: Request, status_: str | None = Query(None, alias="status"), org: str | None = None,
                     name: str | None = None, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    rows = await get_service(request).list_repos(status=status_, org=org, name=name)
    return [r.to_dict() for r in rows]


@router.get("/api/v1/repos/find", tags=["repos"],
            summary="Repos cujo cartão casa com a pergunta (full-text, sem custo de API)")
async def find_repos(request: Request, q: str = Query(..., min_length=1, max_length=500),
                     limit: int = Query(10, ge=1, le=50),
                     _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    try:
        return await get_service(request).find_repos(q, limit=limit)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.get("/api/v1/repos/{name}/card", tags=["repos"],
            summary="Cartão do repo (resumo escrito por agent) e fatos extraídos sem LLM")
async def repo_card(name: str, request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    try:
        info = await get_service(request).repo_card(name)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sem cartão nem fatos para este repo")
    return info


@router.get("/api/v1/xray", tags=["repos"], summary="Inventário e vínculos para o Raio X")
async def xray(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).xray()


@router.get("/api/v1/repos/{name}/skipped", tags=["repos"],
            summary="Arquivos descartados na indexação de um repo, com o motivo")
async def repo_skipped(name: str, request: Request, limit: int = Query(200, ge=1, le=2000),
                       _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).skipped_files(name, limit=limit)


@router.post("/api/v1/repos", tags=["repos"], status_code=201)
async def add_repo(body: RepoIn, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    repo = await audited(request, principal, "repo:add", body.model_dump(), get_service(request).add_repo(
        body.source, name=body.name, branch=body.branch, include=body.include, extra_exclude=body.extra_exclude,
    ))
    return {"name": repo.name, "url": repo.url, "path": repo.path, "branch": repo.branch}


@router.delete("/api/v1/repos/{name}", tags=["repos"])
async def remove_repo(name: str, request: Request, keep_clone: bool = False,
                      principal: Principal = Depends(require(ADMIN))) -> dict[str, str]:
    result = await audited(request, principal, "repo:remove", {"name": name, "keep_clone": keep_clone},
                           get_service(request).remove_repo(name, keep_clone=keep_clone))
    return {"name": name, "result": result}


@router.post("/api/v1/repos/{name}/unignore", tags=["repos"])
async def unignore_repo(name: str, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, str]:
    await audited(request, principal, "repo:unignore", {"name": name}, get_service(request).unignore_repo(name))
    return {"name": name, "result": "pending"}
