"""CLI: `code-index org` (orgs do GitHub e descoberta automática de repos)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers


async def _cmd_org_add(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        stats = await svc.add_org(
            a.name,
            match=a.match or (),
            skip=a.skip or (),
            languages=a.languages or (),
            include_archived=a.include_archived,
            include_forks=a.include_forks,
            max_size_mb=a.max_size_mb,
            extra_exclude=a.exclude or (),
        )
    print(f"org {a.name}: {stats['total']} repositórios descobertos (pendentes de indexação).")
    print("indexe aos poucos com `code-index sync --batch N` ou um a um com `code-index index <nome>`.")


async def _cmd_org_set(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.set_org(
            a.name,
            languages=a.languages,
            match=a.match,
            skip=a.skip,
            max_size_mb=a.max_size_mb,
        )
    print(f"org {a.name}: {st['total']} repos ({st['new']} novos, {st['removed']} removidos pelo filtro).")
    if st["removed"]:
        print("os chunks dos repos removidos somem no próximo `code-index sync`.")


async def _cmd_org_remove(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        n = await svc.remove_org(a.name, keep_clones=a.keep_clones)
    print(f"org {a.name} removida ({n} repos). Os chunks somem no próximo `code-index sync`.")


async def _cmd_org_refresh(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        results = await svc.refresh_orgs(a.name)
    for name, st in results.items():
        print(f"{name}: {st['total']} repos ({st['new']} novos, {st['removed']} removidos)")


async def _cmd_org_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        summary = await svc.org_summary()
    for org in summary:
        st = org["status"]
        filters = ", ".join(f"{k}={v}" for k, v in org["filters"].items())
        print(f"{org['name']:<25} {org['total']:>4} repos | indexed {st['indexed']:>4} | pending {st['pending']:>4} | "
              f"error {st['error']:>3} | ignored {st['ignored']:>3} | desatualizados {org['outdated']:>3}"
              + (f"  [{filters}]" if filters else ""))
        print("    linguagens: " + ", ".join(f"{k} {v}" for k, v in org["languages"].items()))


def register(sub: Subparsers, settings: Settings) -> None:
    org = sub.add_parser("org", help="orgs do GitHub (descoberta automática de repos)").add_subparsers(required=True)
    o = org.add_parser("add", help="adiciona uma org e descobre seus repos (não indexa ainda)")
    o.add_argument("name")
    o.add_argument("--match", nargs="+", help="só repos cujo nome casa com estes globs (ex.: 'api-*')")
    o.add_argument("--skip", nargs="+", help="ignora repos cujo nome casa com estes globs")
    o.add_argument("--include-archived", action="store_true")
    o.add_argument("--include-forks", action="store_true")
    o.add_argument("--max-size-mb", type=int, help="ignora repos maiores que isso (tamanho do git)")
    o.add_argument("--languages", nargs="+",
                   help="só repos com estas linguagens principais (GitHub), em ordem de prioridade; 'none' = sem linguagem")
    o.add_argument("--exclude", nargs="+", help="padrões extras de exclusão de arquivos (glob)")
    o.set_defaults(fn=_cmd_org_add)
    o = org.add_parser("set", help="altera filtros de uma org (e redescobre)")
    o.add_argument("name")
    o.add_argument("--languages", nargs="*", help="linguagens em ordem de prioridade; vazio limpa o filtro")
    o.add_argument("--match", nargs="*")
    o.add_argument("--skip", nargs="*")
    o.add_argument("--max-size-mb", type=int, help="0 remove o limite")
    o.set_defaults(fn=_cmd_org_set)
    o = org.add_parser("remove")
    o.add_argument("name")
    o.add_argument("--keep-clones", action="store_true")
    o.set_defaults(fn=_cmd_org_remove)
    o = org.add_parser("refresh", help="redescobre repos (novos/removidos/pushes)")
    o.add_argument("name", nargs="?")
    o.set_defaults(fn=_cmd_org_refresh)
    org.add_parser("list", help="orgs e progresso da indexação").set_defaults(fn=_cmd_org_list)
