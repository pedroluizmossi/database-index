"""CLI: `code-index tests` (casos de teste lidos dos clones, sem embedding)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


async def _cmd_scan(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.scan_tests(a.repo or None, force=a.force)
        st = await svc.tests_status()
    if a.json:
        print_json({**res, "status": st})
        return
    print(f"{res['repos']} repo(s) lidos agora, {res['cases']} caso(s); {res['pruned']} repo(s) saíram.")
    print(f"no total: {st['cases']} casos em {st['files']} arquivos de teste de {st['repos']} repos, "
          f"{st['linked']} ligados ao código testado.")


async def _cmd_find(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.find_tests(" ".join(a.target), repos=a.repo, limit=a.limit)
    if a.json:
        print_json(res)
        return
    if not res["tests"]:
        print("nenhum teste encontrado.")
        return
    print(f"{res['total']} caso(s), pelo {res['matched_by']}\n")
    for t in res["tests"]:
        extra = f"  → {t['tests_file']}" if t.get("tests_file") else ""
        calls = f"  [{', '.join(t['calls'])}]" if t.get("calls") else ""
        print(f"\033[1m{t['repo']}/{t['path']}:{t['line']}\033[0m  {t['name']}{extra}{calls}")
    if res["truncated"]:
        print(f"... mais {res['total'] - len(res['tests'])} (use -n).")


async def _cmd_status(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.tests_status()
    print_json(st)


def register(sub: Subparsers, settings: Settings) -> None:
    t = sub.add_parser("tests", help="casos de teste lidos dos clones (sem embedding): ler, procurar, estado")
    ts = t.add_subparsers(required=True)
    sc = ts.add_parser("scan", help="lê os testes dos repos que mudaram (o sync já faz; sem custo de API)")
    sc.add_argument("repo", nargs="*", help="só estes repos")
    sc.add_argument("--force", action="store_true", help="relê mesmo sem mudança")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=_cmd_scan)
    fd = ts.add_parser("find", help="testes que cobrem um arquivo, classe ou método")
    fd.add_argument("target", nargs="+", help="caminho, Classe, Classe.metodo, metodo ou parte do nome do teste")
    fd.add_argument("-r", "--repo", action="append", help="filtra por repo (pode repetir)")
    fd.add_argument("-n", "--limit", type=int, default=30)
    fd.add_argument("--json", action="store_true")
    fd.set_defaults(fn=_cmd_find)
    st = ts.add_parser("status", help="quantos repos, arquivos e casos foram lidos")
    st.set_defaults(fn=_cmd_status)
