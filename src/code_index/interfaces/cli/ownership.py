"""CLI: `code-index owner` (domínio/serviço do DDD dono de cada repo ou pasta)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json

_ORIGIN_LABEL = {"manual": "manual", "declared": "SDL", "sibling": "irmão", "api_calls": "chamadas", "usage": "uso",
                 "none": "sem dono"}


async def _cmd_owner_catalog(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.ownership_catalog()
    if a.json:
        print_json(res)
        return
    cov = res["coverage"]
    origins = ", ".join(f"{_ORIGIN_LABEL.get(o, o)} {n}" for o, n in cov["by_origin"].items())
    print(f"cobertura: {cov['owned']}/{cov['repos']} repos com dono ({cov['percent']}%) · {origins}")
    for d in res["domains"]:
        print(f"\033[1m{d['name']}\033[0m" + (f" ({d['label']})" if d["label"] else "") + f"  {d['repos']} repo(s)")
        for svc_ in d["services"]:
            extra = " [criado à mão]" if svc_["manual"] else ""
            label = f" ({svc_['label']})" if svc_["label"] else ""
            print(f"  {d['name']}/{svc_['name']}{label}  {svc_['repos']} repo(s){extra}")


async def _cmd_owner_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.ownership_list(domain=a.domain, service=a.service, origin=a.origin, unowned=a.sem_dono,
                                        repo=a.repo)
    if a.json:
        print_json(rows)
        return
    for r in rows:
        name = r["repo"] + (f"/{r['path']}/" if r["path"] else "")
        if not r["owners"]:
            print(f"{name:<60} —")
            continue
        alt = f"  (alternativas: {', '.join(x['owner'] for x in r['alternatives'])})" if r["alternatives"] else ""
        conf = f", {r['confidence']}" if r["confidence"] else ""
        print(f"{name:<60} {', '.join(r['owners'])}  [{_ORIGIN_LABEL.get(r['origin'], r['origin'])}{conf}]{alt}")
    print(f"\n{len(rows)} linha(s).")


async def _cmd_owner_set(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.set_owner(None, a.repos, a.to, path=a.path, create=a.criar, reason=a.motivo or "")
        await svc.audit(None, "owner:set:local", params={"owner": a.to, "path": a.path, "n": res["updated"]})
    print(f"dono manual {res['owner']} gravado em {res['updated']} entidade(s): {', '.join(res['entities'][:10])}")


async def _cmd_owner_clear(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.clear_owner(None, a.repos, path=a.path)
        await svc.audit(None, "owner:clear:local", params={"path": a.path, "n": res["cleared"]})
    print(f"dono manual removido de {res['cleared']} entidade(s); valem de novo o SDL e a inferência.")


def register(sub: Subparsers, settings: Settings) -> None:
    ow = sub.add_parser("owner", help="domínio/serviço (DDD) dono de cada repo ou pasta").add_subparsers(required=True)
    o_ = ow.add_parser("catalog", help="domínios e serviços (do SDL e criados à mão) e a cobertura")
    o_.add_argument("--json", action="store_true")
    o_.set_defaults(fn=_cmd_owner_catalog)
    o_ = ow.add_parser("list", help="dono de cada repo (e pasta), com a origem e as alternativas")
    o_.add_argument("--domain")
    o_.add_argument("--service")
    o_.add_argument("--origin", choices=sorted(set(_ORIGIN_LABEL) - {"none"}), help="só desta origem")
    o_.add_argument("--sem-dono", action="store_true", help="só repos sem dono (para revisar)")
    o_.add_argument("--repo")
    o_.add_argument("--json", action="store_true")
    o_.set_defaults(fn=_cmd_owner_list)
    o_ = ow.add_parser("set", help="define o dono manual (vence a inferência; vale na hora)")
    o_.add_argument("repos", nargs="+")
    o_.add_argument("--to", required=True, help="dominio ou dominio/servico (ex.: hcm/payroll, transversal)")
    o_.add_argument("--path", help="pasta do repo (monorepo); só com um repo")
    o_.add_argument("--criar", action="store_true", help="cria o domínio/serviço se não existir no catálogo")
    o_.add_argument("--motivo")
    o_.set_defaults(fn=_cmd_owner_set)
    o_ = ow.add_parser("clear", help="remove o dono manual (volta ao SDL/inferência)")
    o_.add_argument("repos", nargs="+")
    o_.add_argument("--path")
    o_.set_defaults(fn=_cmd_owner_clear)
