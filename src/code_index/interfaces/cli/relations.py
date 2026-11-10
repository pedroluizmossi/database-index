"""CLI: `code-index relations` (relações entre repos, documentos, operações de API e código)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


async def _cmd_relations_rebuild(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.rebuild_relations()
    docs = ", ".join(f"{k} {v}" for k, v in res["documents"].items()) or "nenhuma fonte"
    print(f"documentos: {docs}")
    print("linkers: " + ", ".join(f"{k} {v}" for k, v in res["linkers"].items()))
    print(f"{res['relations']} relações derivadas em {res['seconds']}s (sem LLM e sem custo).")


async def _cmd_relations_stats(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.relation_stats()
    if a.json:
        print_json(st, default=str)
        return
    print("efetivas: " + ", ".join(f"{k} {v}" for k, v in sorted(st["effective"].items())))
    for r in st["rows"]:
        extra = "" if (r["status"], r["action"]) == ("active", "add") else f" [{r['status']}/{r['action']}]"
        print(f"  {r['type']:<15} {r['origin']:<10} {r['n']:>6}{extra}")


async def _cmd_relations_show(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.related(a.entity, type_=a.type, limit=a.limit)
    if a.json:
        print_json(res)
        return
    print(f"\033[1m{res['entity']}\033[0m")
    if not res["relations"]:
        print("  nenhuma relação.")
    for r in res["relations"]:
        arrow = "→" if r["direction"] == "out" else "←"
        print(f"  {arrow} {r['type']:<15} {r['entity']}  ({', '.join(r['origins'])})")


def register(sub: Subparsers, settings: Settings) -> None:
    rl = sub.add_parser("relations", help="relações entre repos, documentos, operações de API e código") \
        .add_subparsers(required=True)
    rl.add_parser("rebuild", help="recalcula documentos e relações derivadas (sem custo; o sync já faz)") \
        .set_defaults(fn=_cmd_relations_rebuild)
    r_ = rl.add_parser("stats", help="quantas relações por tipo e origem")
    r_.add_argument("--json", action="store_true")
    r_.set_defaults(fn=_cmd_relations_stats)
    r_ = rl.add_parser("show", help="tudo ligado a uma entidade (repo, doc:..., op:..., code:...)")
    r_.add_argument("entity", help="nome do repo ou chave, ex.: op:platform/email/sendEmail")
    r_.add_argument("-t", "--type", choices=["documents", "describes", "implemented_by", "related_to", "belongs_to"])
    r_.add_argument("-n", "--limit", type=int, default=200)
    r_.add_argument("--json", action="store_true")
    r_.set_defaults(fn=_cmd_relations_show)
