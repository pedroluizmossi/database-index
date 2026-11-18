"""CLI: `code-index graph` (grafo de chamadas lido dos clones com tree-sitter, sem embedding)."""

from __future__ import annotations

import argparse

from ...settings import Settings
from .common import Subparsers, open_backend, print_json


async def _cmd_scan(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        res = await svc.scan_calls(a.repo or None, force=a.force)
        st = await svc.graph_status()
    if a.json:
        print_json({**res, "status": st})
        return
    print(f"{res['repos']} repo(s) lidos agora, {res['edges']} aresta(s); {res['pruned']} repo(s) saíram.")
    pct = st["resolved"] / st["calls"] if st["calls"] else 0
    print(f"no total: {st['edges']} arestas em {st['repos']} repos ({st['files']} arquivos); "
          f"{st['resolved']} de {st['calls']} chamadas ligadas ({pct:.0%}), {st['inferred']} só pelo nome.")


async def _show(a: argparse.Namespace, settings: Settings, direction: str) -> None:
    async with open_backend(settings) as svc:
        res = await svc.call_graph(" ".join(a.target), direction=direction, repos=a.repo, limit=a.limit)
    if a.json:
        print_json(res)
        return
    if not res["edges"]:
        print("nenhuma chamada encontrada.")
        return
    label = "chamado por" if direction == "callers" else "chama"
    print(f"{res['total']} aresta(s)\n")
    for e in res["edges"]:
        times = f" ×{e['calls']}" if e["calls"] > 1 else ""
        mark = " (inferida)" if e["inferred"] else ""
        print(f"\033[1m{e['of']['name']}\033[0m {label} \033[1m{e['name']}\033[0m  "
              f"{e['repo']}/{e['path']}:{e['line']}  [{e['via']}{mark}]{times}")
    if res["truncated"]:
        print(f"... mais {res['total'] - len(res['edges'])} (use -n).")


async def _cmd_callers(a: argparse.Namespace, settings: Settings) -> None:
    await _show(a, settings, "callers")


async def _cmd_callees(a: argparse.Namespace, settings: Settings) -> None:
    await _show(a, settings, "callees")


async def _cmd_status(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        print_json(await svc.graph_status())


def register(sub: Subparsers, settings: Settings) -> None:
    g = sub.add_parser("graph", help="grafo de chamadas lido dos clones (sem embedding): ler, quem chama, estado")
    gs = g.add_subparsers(required=True)
    sc = gs.add_parser("scan", help="lê as chamadas dos repos que mudaram (o sync já faz; sem custo de API)")
    sc.add_argument("repo", nargs="*", help="só estes repos")
    sc.add_argument("--force", action="store_true", help="relê mesmo sem mudança")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=_cmd_scan)
    for name, fn, help_ in (("callers", _cmd_callers, "quem chama um símbolo"),
                            ("callees", _cmd_callees, "o que um símbolo chama")):
        p = gs.add_parser(name, help=help_)
        p.add_argument("target", nargs="+", help="Classe.metodo, metodo, caminho/arquivo ou caminho:linha")
        p.add_argument("-r", "--repo", action="append", help="filtra por repo (pode repetir)")
        p.add_argument("-n", "--limit", type=int, default=30)
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=fn)
    st = gs.add_parser("status", help="quantos repos, chamadas e arestas foram lidos")
    st.set_defaults(fn=_cmd_status)
