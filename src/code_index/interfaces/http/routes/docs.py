"""Documentação: fontes e seleção da wiki, páginas fora da busca, vínculos página → repo e sugestões."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited, http_error
from ..security import get_service, require

router = APIRouter()


class PageRef(BaseModel):
    """Uma página de documentação: pela chave (doc_repo + doc_path com idioma e .md, como em /docs/overview) ou,
    como a CLI, pela fonte + caminho sem idioma (source + page, ex.: wiki + Plataforma/Email)."""

    doc_repo: str | None = Field(None, min_length=1, max_length=200)
    doc_path: str | None = Field(None, min_length=1, max_length=1000)
    source: str | None = Field(None, min_length=1, max_length=200, description="fonte, ex.: wiki ou docs__wiki")
    page: str | None = Field(None, min_length=1, max_length=1000, description="caminho sem idioma, ex.: Plataforma/Email")

    @model_validator(mode="after")
    def _one_form(self) -> "PageRef":
        _check_page_ref(self.doc_repo, self.doc_path, self.source, self.page)
        return self


def _check_page_ref(doc_repo: str | None, doc_path: str | None, source: str | None, page: str | None) -> bool:
    """Valida que veio exatamente uma das formas; devolve True se é a forma da CLI (source + page)."""
    by_key, by_page = bool(doc_repo and doc_path), bool(source and page)
    if by_key == by_page or (by_key and (source or page)) or (by_page and (doc_repo or doc_path)):
        raise ValueError("informe doc_repo + doc_path OU source + page")
    return by_page


class DocLinkIn(PageRef):
    repo: str = Field(min_length=1, max_length=200)
    reason: str = Field("", max_length=500)
    origin: Literal["manual", "agent"] | None = Field(
        None, description="só para sessão/token de pessoa; token de agent grava sempre 'agent'")


class DocLinkRuleIn(BaseModel):
    path: str = Field(min_length=1, max_length=500, description="glob de caminho de página, ex.: Plataforma/email/**")
    repos: list[str] = Field(min_length=1, max_length=100, description="globs de repo, ex.: *email*")


class DocSourceIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
                      description="nome curto; o repo de documentação vira docs__<nome>")
    url: str = Field(min_length=1, max_length=500, description="base da wiki, ex.: https://wiki.exemplo.com")
    locale: str = Field("pt-br", min_length=1, max_length=20)
    include: list[str] = Field([], max_length=500)
    exclude: list[str] = Field([], max_length=500)
    links: list[DocLinkRuleIn] = Field([], max_length=500)


class SuggestionApproveIn(BaseModel):
    confidence: Literal["alta", "media", "baixa"]
    source: str | None = Field(None, max_length=200)
    action: Literal["add", "remove"] | None = None


class SuggestionReviewIn(BaseModel):
    doc_repo: str = Field(min_length=1, max_length=200)
    doc_path: str = Field(min_length=1, max_length=1000)
    repo: str = Field(min_length=1, max_length=200)
    approve: bool


class DocSelectionIn(BaseModel):
    pages: list[str] = Field(max_length=50_000, description="caminhos de página sem idioma, ex.: Plataforma/Email")
    dry_run: bool = Field(False, description="só calcula as regras, sem gravar")


class DocIgnoreIn(PageRef):
    reason: str = Field("", max_length=500)


def _query_ref(doc_repo: str | None, doc_path: str | None, source: str | None, page: str | None) -> bool:
    try:
        return _check_page_ref(doc_repo, doc_path, source, page)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.get("/api/v1/docs/{name}/preview", tags=["docs"],
            summary="Prévia das páginas de uma fonte de documentação (sem custo)")
async def docs_preview(name: str, request: Request, limit: int = Query(200, ge=1, le=1000),
                       _: Principal = Depends(require(SEARCH_READ))) -> dict[str, Any]:
    return await get_service(request).docs_preview(name, limit=limit)


@router.get("/api/v1/docs/sources", tags=["docs"],
            summary="Fontes de documentação: seleção (include/exclude) e documentos no índice")
async def doc_sources(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).doc_sources()


@router.post("/api/v1/docs/sources", tags=["docs"], status_code=201,
             summary="Cadastra uma wiki (valida o acesso; não indexa nem baixa páginas) (admin)")
async def add_doc_source(body: DocSourceIn, request: Request,
                         principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    """Resposta: name (docs__<nome>), pages (selecionadas) e pages_in_locale. Indexe depois com POST /api/v1/sync."""
    params = body.model_dump()
    result: dict[str, Any] = await audited(request, principal, "docs:add", params, get_service(request).add_docs(
        body.name, url=body.url, locale=body.locale, include=body.include, exclude=body.exclude,
        links=[(rule.path, rule.repos) for rule in body.links], principal=principal,
    ))
    return result


@router.delete("/api/v1/docs/sources/{name}", tags=["docs"],
               summary="Remove uma fonte de documentação; os trechos somem no próximo sync (admin)")
async def remove_doc_source(name: str, request: Request, keep_files: bool = False,
                            principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    await audited(request, principal, "docs:remove", {"name": name, "keep_files": keep_files},
                  get_service(request).remove_docs(name, keep_files=keep_files))
    return {"name": name, "removed": True}


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
    """Aceita a chave (doc_repo + doc_path) ou a forma da CLI (source + page; a resposta traz `page`)."""
    svc = get_service(request)
    if body.source and body.page:
        fn = svc.ignore_doc(body.source, body.page, reason=body.reason)
    else:
        assert body.doc_repo and body.doc_path
        fn = svc.ignore_doc_key(body.doc_repo, body.doc_path, body.reason)
    result: dict[str, Any] = await audited(request, principal, "docs:ignore", body.model_dump(exclude_none=True), fn)
    return result


@router.delete("/api/v1/docs/ignored", tags=["docs"], summary="Devolve uma página à busca (admin)")
async def unignore_doc(request: Request, doc_repo: str | None = Query(None, max_length=200),
                       doc_path: str | None = Query(None, max_length=1000),
                       source: str | None = Query(None, max_length=200), page: str | None = Query(None, max_length=1000),
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
    svc = get_service(request)
    if _query_ref(doc_repo, doc_path, source, page):
        assert source and page
        params, fn = {"source": source, "page": page}, svc.unignore_doc(source, page)
    else:
        assert doc_repo and doc_path
        params, fn = {"doc_repo": doc_repo, "doc_path": doc_path}, svc.unignore_doc_key(doc_repo, doc_path)
    removed = await audited(request, principal, "docs:unignore", params, fn)
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


@router.post("/api/v1/docs/suggestions/approve", tags=["docs"],
             summary="Aprova em lote as sugestões pendentes de uma confiança (admin)")
async def approve_doc_suggestions(body: SuggestionApproveIn, request: Request,
                                  principal: Principal = Depends(require(ADMIN))) -> dict[str, int]:
    n = await audited(request, principal, "docs:suggestion:approve", body.model_dump(),
                      get_service(request).approve_doc_suggestions(confidence=body.confidence, source=body.source,
                                                                   action=body.action))
    return {"approved": int(n)}


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
    """Aceita a chave (doc_repo + doc_path) ou a forma da CLI (source + page; a resposta traz `page`)."""
    origin = "agent" if principal.kind == "agent" else (body.origin or "manual")
    svc = get_service(request)
    if body.source and body.page:
        fn = svc.link_doc(body.source, body.page, body.repo, reason=body.reason, origin=origin)
    else:
        assert body.doc_repo and body.doc_path
        fn = svc.link_doc_key(body.doc_repo, body.doc_path, body.repo, reason=body.reason, origin=origin)
    result: dict[str, Any] = await audited(request, principal, "docs:link",
                                           {**body.model_dump(exclude_none=True), "origin": origin}, fn)
    return result


@router.get("/api/v1/docs/links", tags=["docs"],
            summary="Vínculos página → repo gravados (manual, agent ou sugestão aprovada)")
async def doc_links(request: Request, source: str | None = Query(None, max_length=200),
                    repo: str | None = Query(None, max_length=200),
                    _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    try:
        return await get_service(request).doc_links(source=source, repo=repo)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None


@router.delete("/api/v1/docs/links", tags=["docs"], summary="Remove um vínculo gravado (admin)")
async def remove_doc_link(request: Request, repo: str = Query(..., max_length=200),
                          doc_repo: str | None = Query(None, max_length=200),
                          doc_path: str | None = Query(None, max_length=1000),
                          source: str | None = Query(None, max_length=200), page: str | None = Query(None, max_length=1000),
                          principal: Principal = Depends(require(ADMIN))) -> dict[str, bool]:
    svc = get_service(request)
    if _query_ref(doc_repo, doc_path, source, page):
        assert source and page
        params, fn = {"source": source, "page": page, "repo": repo}, svc.unlink_doc(source, page, repo)
    else:
        assert doc_repo and doc_path
        params = {"doc_repo": doc_repo, "doc_path": doc_path, "repo": repo}
        fn = svc.unlink_doc_key(doc_repo, doc_path, repo)
    removed = await audited(request, principal, "docs:unlink", params, fn)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "vínculo não encontrado")
    return {"removed": True}
