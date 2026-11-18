"""CLI: `code-index api` (catálogo Swagger no S3 e onde as operações são implementadas)."""

from __future__ import annotations

import argparse

from ...domain.api_impl import NO_IMPL_CONVENTION, ImplConvention
from ...services.base import prepare_profiles
from ...settings import Settings
from .common import Subparsers, as_remote, open_backend, print_json


async def _cmd_api(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        if a.api_command == "domains":
            catalog = await svc.openapi_catalog()
            for domain in sorted({x["domain"] for x in catalog}):
                print(domain)
        elif a.api_command == "services":
            catalog = await svc.openapi_catalog()
            for row in catalog:
                if row["domain"] == a.domain:
                    print(row["service"])
        else:
            preview = await svc.openapi_preview(a.domain, a.service)
            print_json(preview)
            if not a.dry_run:
                remote = as_remote(svc)
                if remote is not None:  # o servidor registra e enfileira; o job roda no worker
                    job = await remote.openapi_index(a.domain, a.service)
                    result = await remote.job_result(job["job_id"])
                else:
                    name = await svc.register_openapi(a.domain, a.service)
                    result = await svc.sync([name], batch=None, do_discover=False,
                                            doc_pages=frozenset(a.service) if a.service else None)
                print_json(result.to_dict())
                if result.errors:
                    raise SystemExit(1)


def _convention(settings: Settings) -> ImplConvention | None:
    """Convenção dos perfis ativos (local); perfil desconhecido não impede montar a CLI (o erro sai no comando)."""
    try:
        return prepare_profiles(settings).impl_convention
    except ValueError:
        return None


async def _cmd_api_impl(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        rows = await svc.find_api_impl(" ".join(a.operation), service=a.service, domain=a.domain, limit=a.limit)
    if a.json:
        print_json(rows)
        return
    if not rows:
        c = _convention(settings)
        print(f"nenhuma implementação encontrada (operações de contrato casadas pela {c.rule}; rode "
              "`code-index enrich facts`)." if c is not None else f"nenhuma implementação: {NO_IMPL_CONVENTION}.")
    for r in rows:
        print(f"\033[1m{r['domain']}/{r['service']} {r['kind']} {r['operation']}\033[0m  → "
              f"{r['repo']}/{r['path']}:{r['line']}  ({r['symbol']}, por {r['origin']})")


def register(sub: Subparsers, settings: Settings) -> None:
    api_cmd = sub.add_parser("api", help="catálogo Swagger no S3").add_subparsers(dest="api_command", required=True)
    api_cmd.add_parser("domains", help="lista domínios sem indexar").set_defaults(fn=_cmd_api)
    ap = api_cmd.add_parser("services", help="lista serviços sem indexar")
    ap.add_argument("--domain", required=True)
    ap.set_defaults(fn=_cmd_api)
    c = _convention(settings)
    rule = c.rule if c is not None else "convenção de nomes do perfil de organização ativo"
    ap = api_cmd.add_parser("impl", help=f"onde uma operação da API é implementada ({rule}; sem custo)")
    ap.add_argument("operation", nargs="+", help="op, servico.op ou 'POST /servico/queries/op'")
    ap.add_argument("-s", "--service")
    ap.add_argument("-d", "--domain")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.set_defaults(fn=_cmd_api_impl)
    ap = api_cmd.add_parser("index", help="indexa domínio ou serviços selecionados")
    ap.add_argument("--domain", required=True)
    ap.add_argument("--service", action="append", help="serviço exato; pode repetir")
    ap.add_argument("--dry-run", action="store_true")
    ap.set_defaults(fn=_cmd_api)
