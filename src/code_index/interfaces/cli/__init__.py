"""CLI: code-index {org, repo, sync|index, watch, worker, search, show, enrich, relations, ...}.

Só parsing e formatação: as regras de negócio estão em `services.CodeIndexService`.
Cada área tem um módulo com `register(sub, settings)` (subcomandos) e os seus `_cmd_*`.
Cada comando roda em UM `asyncio.run` dentro de `open_service()`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ...settings import bootstrap
from . import (
    apis,
    code,
    docs,
    enrich,
    evals,
    orgs,
    ownership,
    relations,
    repos,
    search,
    serve,
    sync,
    tokens,
    usage,
    users,
)
from .common import CommandFn

# Ordem do `--help`.
_AREAS = (orgs, repos, sync, search, evals, apis, docs, code, enrich, relations, ownership, serve, tokens, users, usage)


def main() -> None:
    settings = bootstrap()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="code-index", description="Indexador de código com CocoIndex + Voyage + pgvector")
    sub = p.add_subparsers(required=True)
    for area in _AREAS:
        area.register(sub, settings)

    a = p.parse_args()
    fn: CommandFn = a.fn
    try:
        asyncio.run(fn(a, settings))
    except KeyboardInterrupt:
        sys.exit(130)  # interrompido (Ctrl-C): não conta como sucesso para scripts e cron
    except (ValueError, RuntimeError, PermissionError) as e:
        sys.exit(f"erro: {e}")
