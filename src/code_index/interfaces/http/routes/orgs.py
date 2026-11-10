"""Organizações do GitHub (descoberta de repos): listar, cadastrar, ajustar, remover e atualizar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ....domain.auth import ADMIN, SEARCH_READ, Principal
from ..deps import audited
from ..security import get_service, require

router = APIRouter()


class OrgIn(BaseModel):
    name: str
    match: list[str] = []
    skip: list[str] = []
    languages: list[str] = []
    include_archived: bool = False
    include_forks: bool = False
    max_size_mb: int | None = None
    extra_exclude: list[str] = []


class OrgPatch(BaseModel):
    languages: list[str] | None = None
    match: list[str] | None = None
    skip: list[str] | None = None
    max_size_mb: int | None = Field(None, description="0 remove o limite")


@router.get("/api/v1/orgs", tags=["orgs"])
async def list_orgs(request: Request, _: Principal = Depends(require(SEARCH_READ))) -> list[dict[str, Any]]:
    return await get_service(request).org_summary()


@router.post("/api/v1/orgs", tags=["orgs"], status_code=201)
async def add_org(body: OrgIn, request: Request, principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    params = body.model_dump()
    name = params.pop("name")
    return await audited(request, principal, "org:add", body.model_dump(), get_service(request).add_org(name, **params))


@router.patch("/api/v1/orgs/{name}", tags=["orgs"])
async def set_org(name: str, body: OrgPatch, request: Request,
                  principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await audited(request, principal, "org:set", {"name": name, **body.model_dump()},
                         get_service(request).set_org(name, **body.model_dump()))


@router.delete("/api/v1/orgs/{name}", tags=["orgs"])
async def remove_org(name: str, request: Request, keep_clones: bool = False,
                     principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    n = await audited(request, principal, "org:remove", {"name": name, "keep_clones": keep_clones},
                      get_service(request).remove_org(name, keep_clones=keep_clones))
    return {"name": name, "removed_repos": n}


@router.post("/api/v1/orgs/refresh", tags=["orgs"])
async def refresh_orgs(request: Request, name: str | None = None,
                       principal: Principal = Depends(require(ADMIN))) -> dict[str, Any]:
    return await audited(request, principal, "org:refresh", {"name": name}, get_service(request).refresh_orgs(name))
