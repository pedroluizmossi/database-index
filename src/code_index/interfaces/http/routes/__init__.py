"""Rotas HTTP por área (espelham `services/`). Cada módulo expõe um `router`; o `create_app` inclui todos."""

from __future__ import annotations

from fastapi import APIRouter

from . import apis, auth, docs, enrich, graph, orgs, ownership, relations, repos, search, sync, tokens, usage, users, vectors

ROUTERS: tuple[APIRouter, ...] = (
    auth.router, users.router, search.router, graph.router, repos.router, ownership.router, apis.router, docs.router,
    relations.router, orgs.router, enrich.router, sync.router, vectors.router, tokens.router, usage.router,
)
