"""Documentação: fontes e seleção da wiki, páginas fora da busca, vínculos página → repo e sugestões."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited, http_error
from ..security import get_service, require

router = APIRouter()


class DocLinkIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
    reason: str = Field("", max_length=500)


class SuggestionReviewIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
    approve: bool


class DocSelectionIn(BaseModel):
    pages: list[str] = Field(max_length=50_000, description="caminhos de página sem idioma, ex.: Plataforma/Email")
    dry_run: bool = Field(False, description="só calcula as regras, sem gravar")


class DocIgnoreIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    reason: str = Field("", max_length=500)


@router.get("/api/v1/docs/{name}/preview", tags=["docs"],
            summary="Prévia das páginas de uma fonte de documentação (sem custo)")
async def docs_preview(name: str, request: Request, limit: int = Query(200, ge=1, le=1000),
                       _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).docs_preview(name, limit=limit)


@router.get("/api/v1/docs/sources", tags=["docs"],
            summary="Fontes de documentação: seleção (include/exclude) e documentos no índice")
async def doc_sources(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).doc_sources()


@router.get("/api/v1/docs/{name}/catalog", tags=["docs"],
            summary="Todas as páginas da wiki, marcando as da seleção e as do índice (admin; sem custo)")
async def doc_catalog(name: str, request: Request, _: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    try:
        return await get_service(request).wiki_catalog(name)
    except (ValueError, RuntimeError) as e:
        raise http_error(e) from None


@router.put("/api/v1/docs/{name}/selection", tags=["docs"],
            summary="Troca as páginas escolhidas da wiki (admin; não indexa, vale no próximo sync)")
async def doc_selection(name: str, body: DocSelectionIn, request: Request,
                        principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    params = {"name": name, "pages": len(body.pages), "dry_run": body.dry_run}
    return await audited(request, principal, "docs:selection", params, get_service(request).set_wiki_selection(
        name, body.pages, dry_run=body.dry_run))


@router.post("/api/v1/docs/ignored", tags=["docs"], summary="Tira uma página da busca, sem tirar do índice (admin)")
async def ignore_doc(body: DocIgnoreIn, request: Request,
                     principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await audited(request, principal, "docs:ignore", body.model_dump(),
                         get_service(request).ignore_doc_key(body.doc_repo, body.doc_path, body.reason))


@router.delete("/api/v1/docs/ignored", tags=["docs"], summary="Devolve uma página à busca (admin)")
async def unignore_doc(request: Request, doc_repo: str = Query(..., max_length=200),
                       doc_path: str = Query(..., max_length=1000),
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
    params = {"doc_repo": doc_repo, "doc_path": doc_path}
    removed = await audited(request, principal, "docs:unignore", params,
                            get_service(request).unignore_doc_key(doc_repo, doc_path))
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "página não estava fora da busca")
    return {"ignored": False}


@router.get("/api/v1/docs/suggestions", tags=["docs"],
            summary="Vínculos página → repo sugeridos por LLM (para revisar)")
async def doc_suggestions(request: Request, status_: str = Query("suggested", alias="status"),
                          source: str | None = None, confidence: str | None = None,
                          action: str | None = None, limit: int = Query(200, ge=1, le=2000),
                          _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    try:
        return await get_service(request).doc_suggestions(status=status_, source=source, confidence=confidence,
                                                          action=action, limit=limit)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.post("/api/v1/docs/suggestions/review", tags=["docs"],
             summary="Aprova ou rejeita uma sugestão de vínculo (admin)")
async def review_doc_suggestion(body: SuggestionReviewIn, request: Request,
                                principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
    ok = await audited(request, principal, "docs:suggestion", body.model_dump(),
                       get_service(request).review_doc_suggestion(body.doc_repo, body.doc_path, body.repo,
                                                                  approve=body.approve))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sugestão não encontrada (ou já revisada)")
    return {"ok": True}


@router.get("/api/v1/docs/overview", tags=["docs"],
            summary="Páginas de documentação indexadas, com os repos ligados a cada uma")
async def docs_overview(request: Request, limit: int = Query(500, ge=1, le=2000),
                        offset: int = Query(0, ge=0), docs_only: bool = False,
                        _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).docs_overview(limit=limit, offset=offset, docs_only=docs_only)


@router.get("/api/v1/repos/{name}/docs", tags=["docs"], summary="Páginas de documentação ligadas a um repo")
async def repo_docs(name: str, request: Request, limit: int = Query(100, ge=1, le=500),
                    _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).linked_docs(name, limit=limit)


@router.post("/api/v1/docs/links", tags=["docs"], status_code=201,
             summary="Grava vínculo manual ou de agent entre uma página e um repo (admin)")
async def add_doc_link(body: DocLinkIn, request: Request,
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    origin = "agent" if principal.kind == "agent" else "manual"
    return await audited(request, principal, "docs:link", body.model_dump(), get_service(request).link_doc_key(
        body.doc_repo, body.doc_path, body.repo, reason=body.reason, origin=origin,
    ))


@router.delete("/api/v1/docs/links", tags=["docs"], summary="Remove um vínculo gravado (admin)")
async def remove_doc_link(request: Request, doc_repo: str = Query(..., max_length=200),
                          doc_path: str = Query(..., max_length=1000), repo: str = Query(..., max_length=200),
                          principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
    params = {"doc_repo": doc_repo, "doc_path": doc_path, "repo": repo}
    removed = await audited(request, principal, "docs:unlink", params,
                            get_service(request).unlink_doc_key(doc_repo, doc_path, repo))
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vínculo não encontrado")
    return {"removed": True}
