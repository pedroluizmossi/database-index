"""CLI: code-index {org, repo, sync|index, watch, worker, search, show, enrich, relations, ...}.

Só parsing e formatação: as regras de negócio estão em `services.CodeIndexService`.
Cada área tem um módulo com `register(sub, settings)` (subcomandos) e os seus `_cmd_*`.
Cada comando roda em UM `asyncio.run` dentro de `open_backend()` (common.py): o serviço local (banco) ou, com
servidor configurado (`code-index login` / CODE_INDEX_URL), o cliente de /api/v1 (remote.py), com a mesma saída.
Comandos de operador (`@local_only`) recusam o modo remoto; `serve`, `worker` e `db` sempre usam o banco.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ...settings import Settings, bootstrap
from . import (
    apis,
    code,
    db,
    docs,
    enrich,
    evals,
    graph,
    login,
    orgs,
    ownership,
    relations,
    repos,
    search,
    serve,
    sync,
    tests,
    tokens,
    usage,
    users,
    vectors,
)
from .common import LOCAL_ONLY_ATTR, CommandFn
from .remote import resolve_target

# Ordem do `--help`.
_AREAS = (orgs, repos, sync, vectors, search, evals, apis, docs, code, tests, graph, enrich, relations, ownership, serve, db, login, tokens, users, usage)


def _refuse_local_only(fn: CommandFn, settings: Settings) -> None:
    """Comando de operador (banco direto) no modo remoto: erro claro, sem tentar nada."""
    hint = getattr(fn, LOCAL_ONLY_ATTR, None)
    if hint is None:
        return
    target = resolve_target(settings)
    if target is not None:
        raise ValueError(f"este comando não roda no modo remoto ({target.url}): {hint}. Para o banco direto, rode-o "
                         "no cluster (`kubectl exec -it <pod do worker> -- code-index ...`) ou use `code-index "
                         "--local ...` numa máquina com acesso ao banco.")


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="code-index", description="Indexador de código com CocoIndex + Voyage + pgvector")
    p.add_argument("--local", action="store_true",
                   help="ignora o servidor configurado (login/CODE_INDEX_URL) e abre o banco direto")
    sub = p.add_subparsers(required=True)
    for area in _AREAS:
        area.register(sub, settings)
    return p


def main() -> None:
    settings = bootstrap()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # cada requisição em INFO: ruído na CLI remota
    a = build_parser(settings).parse_args()
    if a.local:
        settings = settings.model_copy(update={"cli_mode": "local"})
    fn: CommandFn = a.fn
    try:
        _refuse_local_only(fn, settings)
        asyncio.run(fn(a, settings))
    except KeyboardInterrupt:
        sys.exit(130)  # interrompido (Ctrl-C): não conta como sucesso para scripts e cron
    except (ValueError, RuntimeError, PermissionError) as e:
        sys.exit(f"erro: {e}")
