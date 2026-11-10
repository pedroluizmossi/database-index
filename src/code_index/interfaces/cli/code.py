"""CLI: consultas diretas ao código indexado: `skips`, `symbol`, `grep` e `show`."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


async def _cmd_skips(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.skipped_files(a.repo, limit=a.limit)
    if a.json:
        print_json(rows)
        return
    if not rows:
        print("nenhum arquivo descartado.")
    for r in rows:
        print(f"{r['repo']}/{r['path']}  \033[1m{r['reason']}\033[0m  {r['size_bytes']:,} bytes".replace(",", "."))
    if len(rows) >= a.limit:
        print(f"(mostrando {len(rows)}; use --limit para ver mais)")


async def _cmd_symbol(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        hits = await svc.find_symbol(a.name, kind=a.kind, repos=a.repo, language=a.lang, limit=a.limit)
    if a.json:
        print_json([h.to_dict() for h in hits])
        return
    if not hits:
        print("nenhum símbolo encontrado (a tabela de símbolos é preenchida no sync).")
    for h in hits:
        where = f" em {h.container}" if h.container else ""
        print(f"\033[1m{h.repo}/{h.path}:{h.line}\033[0m  {h.kind} {h.name}{where}")
        print(f"    {h.signature}")


async def _cmd_grep(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.grep(" ".join(a.pattern), repos=a.repo, path_glob=a.path, regex=a.regex,
                             case_sensitive=a.case_sensitive, max_results=a.max)
    if a.json:
        print_json({"matches": [m.to_dict() for m in res.matches], "truncated": res.truncated})
        return
    for m in res.matches:
        print(f"\033[1m{m.repo}/{m.path}:{m.line}\033[0m  {m.text}")
    if res.truncated:
        print(f"... parou em {len(res.matches)} resultados (use -n para mais, ou filtre com -r/-p).")
    elif not res.matches:
        print("nada encontrado.")


async def _cmd_show(a: argparse.Namespace, settings: Settings) -> None:
    repo, _, path = a.target.partition("/")
    rng = None
    if ":" in path:
        path, rng = path.rsplit(":", 1)
    start = end = None
    if rng:
        s, _, e = rng.partition("-")
        start, end = int(s), int(e or s)
    async with open_service(settings) as svc:
        text = await svc.read_file(repo, path, start, end)
    print(text)


def register(sub: Subparsers, settings: Settings) -> None:
    sk = sub.add_parser("skips", help="arquivos descartados na indexação (tamanho, binário...) e o motivo")
    sk.add_argument("repo", nargs="?", help="filtra por repo")
    sk.add_argument("--limit", type=int, default=200)
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(fn=_cmd_skips)
    sy = sub.add_parser("symbol", help="onde uma classe/função/método é declarado")
    sy.add_argument("name", help="nome, parte dele, Classe.metodo ou Classe.* (membros)")
    sy.add_argument("-k", "--kind", choices=["container", "member", "section"])
    sy.add_argument("-r", "--repo", action="append", help="filtra por repo (pode repetir)")
    sy.add_argument("-l", "--lang", help="filtra por linguagem")
    sy.add_argument("-n", "--limit", type=int, default=20)
    sy.add_argument("--json", action="store_true")
    sy.set_defaults(fn=_cmd_symbol)

    g = sub.add_parser("grep", help="texto exato (ou regex) nos repositórios, via ripgrep")
    g.add_argument("pattern", nargs="+")
    g.add_argument("-r", "--repo", action="append", help="filtra por repo (pode repetir)")
    g.add_argument("-p", "--path", help="filtra caminhos, ex.: '**/*.java'")
    g.add_argument("-E", "--regex", action="store_true", help="trata o padrão como regex")
    g.add_argument("-s", "--case-sensitive", action="store_true")
    g.add_argument("-n", "--max", type=int, default=50, help="máximo de linhas (padrão 50)")
    g.add_argument("--json", action="store_true")
    g.set_defaults(fn=_cmd_grep)

    v = sub.add_parser("show", help="mostra arquivo: repo/caminho[:inicio-fim]")
    v.add_argument("target")
    v.set_defaults(fn=_cmd_show)
