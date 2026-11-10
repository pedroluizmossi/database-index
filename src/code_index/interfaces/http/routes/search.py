"""Consulta: busca híbrida, símbolos, grep, leitura de arquivo e resumos de arquivos."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ....domain.auth import SEARCH_READ, Principal
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class GrepIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    repos: list[str] | None = None
    path_glob: str | None = None
    regex: bool = False
    case_sensitive: bool = False
    max_results: int = Field(50, ge=1, le=500)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    repos: list[str] | None = None
    language: str | None = None
    path_glob: str | None = None
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    rerank: bool = True
    kind: Literal["code", "doc"] | None = None
    include_linked_docs: bool = True
    exclude_repos: list[str] | None = Field(None, max_length=50)
    exclude_path_glob: list[str] | None = Field(None, max_length=50)
    max_docs: int | None = Field(None, ge=0, le=50)
    domain: str | None = Field(None, max_length=64)
    service: str | None = Field(None, max_length=64)


@router.post("/api/v1/search", tags=["busca"], summary="Busca híbrida + rerank")
async def search(body: SearchIn, request: Request,
                 principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> list[dict[str, Any]]:
    svc = get_service(request)
    hits = await audited(request, principal, "search", body.model_dump(exclude={"rerank"}), svc.search(
        body.query, top_k=body.top_k, repos=body.repos, language=body.language, path_glob=body.path_glob,
        mode=body.mode, rerank=body.rerank, kind=body.kind, include_linked_docs=body.include_linked_docs,
        exclude_repos=body.exclude_repos, exclude_path_glob=body.exclude_path_glob, max_docs=body.max_docs,
        domain=body.domain, service=body.service,
    ))
    return [h.to_dict() for h in hits]


@router.get("/api/v1/symbols", tags=["busca"], summary="Onde um símbolo é declarado")
async def symbols(request: Request, name: str = Query(min_length=1, max_length=200), kind: str | None = None,
                  repo: list[str] | None = Query(None), language: str | None = None,
                  limit: int = Query(20, ge=1, le=200),
                  principal: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    hits = await audited(request, principal, "symbols", {"name": name, "kind": kind, "repo": repo},
                         get_service(request).find_symbol(name, kind=kind, repos=repo, language=language,
                                                          limit=limit))
    return [h.to_dict() for h in hits]


@router.post("/api/v1/grep", tags=["busca"], summary="Texto exato ou regex nos repositórios")
async def grep(body: GrepIn, request: Request,
               principal: Principal = Depends(require(SEARCH_READ, bucket="search"))) -> dict[str, Any]:
    res = await audited(request, principal, "grep", body.model_dump(exclude={"case_sensitive"}),
                        get_service(request).grep(body.pattern, repos=body.repos, path_glob=body.path_glob,
                                                  regex=body.regex, case_sensitive=body.case_sensitive,
                                                  max_results=body.max_results))
    return {"matches": [m.to_dict() for m in res.matches], "truncated": res.truncated}


@router.get("/api/v1/files/{repo}/{path:path}", tags=["busca"], summary="Lê arquivo (segredos mascarados)")
async def read_file(repo: str, path: str, request: Request, start_line: int | None = Query(None, ge=1),
                    end_line: int | None = Query(None, ge=1),
                    principal: Principal = Depends(require(SEARCH_READ))) -> Response:
    svc = get_service(request)
    try:
        text = await svc.read_file(repo, path, start_line, end_line)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
    return Response(text, media_type="text/plain; charset=utf-8")


@router.get("/api/v1/summaries/coverage", tags=["busca"],
            summary="Cobertura dos resumos de arquivos de código (total e por repo)")
async def summary_coverage(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).summary_coverage()


@router.get("/api/v1/summaries/repo/{repo}", tags=["busca"], summary="Resumos dos arquivos de um repo")
async def repo_summaries(repo: str, request: Request,
                         _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).repo_summaries(repo)


@router.get("/api/v1/summaries/file/{repo}/{path:path}", tags=["busca"],
            summary="Resumo de um arquivo escrito por agent (null se não houver)")
async def file_summary(repo: str, path: str, request: Request,
                       _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any] | None:
    return await get_service(request).file_summary(repo, path)
