"""CLI: `code-index repo` (repositórios avulsos e de orgs)."""

from __future__ import annotations

import argparse
from collections import Counter

from ...settings import Settings
from .common import Subparsers, open_backend, print_json


async def _cmd_repo_add(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        repo = await svc.add_repo(
            a.source,
            name=a.name,
            branch=a.branch,
            include=a.include,
            extra_exclude=a.exclude,
        )
    print(f"adicionado: {repo.name} -> {repo.url or repo.path}")
    print(f"rode `code-index index {repo.name}` para clonar e indexar.")


async def _cmd_repo_remove(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        result = await svc.remove_repo(a.name, keep_clone=a.keep_clone)
    if result == "removed":
        print(f"removido: {a.name}")
    else:
        print(f"{a.name} é de uma org: marcado como ignorado (desfaça com `code-index repo unignore`)")
    print("os chunks somem no próximo `code-index sync`.")


async def _cmd_repo_unignore(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        await svc.unignore_repo(a.name)
    print(f"{a.name} volta à fila de indexação.")


async def _cmd_repo_find(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        hits = await svc.find_repos(" ".join(a.query), limit=a.limit)
    if a.json:
        print_json(hits)
        return
    if not hits:
        print("nenhum repo casou (só repos com fatos/cartão entram; veja `code-index enrich status`).")
    for h in hits:
        print(f"\033[1m{h['repo']}\033[0m  {h['score']:.3f}" + (f"  [{h['kind']}]" if h["kind"] else "")
              + ("  (cartão desatualizado)" if h["stale"] else ""))
        if h["summary"]:
            print(f"    {h['summary'][:300]}")
        if h["tags"]:
            print(f"    tags: {', '.join(h['tags'])}")


async def _cmd_repo_estimate(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        res = await svc.estimate_files(a.repo or None, org=a.org, limit=a.limit, force=a.force, dry_run=a.dry_run)
    if a.json:
        print_json(res)
        return
    for e in res["estimated"]:
        print(f"{e['name']:<60} {'≥' if e['truncated'] else '~'}{e['files']:>6} arq")
    for e in res["errors"]:
        print(f"{e['name']:<60} erro: {e['error']}")
    verb = "faria" if a.dry_run else "fez"
    print(f"\n{verb} {res['calls']} chamada(s) ao GitHub; já contados neste push: {res['cached']}; "
          f"faltam {res['left']}; cota restante: {res['rate_remaining']} (reserva {res['reserve']}).")
    if res["unsupported"]:
        print(f"{res['unsupported']} repo(s) fora do github.com (sem estimativa).")
    if res["stopped"]:
        print(f"parou: {res['stopped']}")


async def _cmd_repo_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_backend(settings) as svc:
        rows = await svc.list_repos(status=a.status, org=a.org, name=a.filter)
    if a.json:
        print_json([r.to_dict() for r in rows])
        return
    if not rows:
        print("nenhum repositório.")
    for r in rows:
        flag = " *desatualizado*" if r.outdated else ""
        when = (r.indexed_at or "")[:16].replace("T", " ")
        files = (f"{'≥' if r.files_estimate_truncated else '~'}{r.files_estimate}"
                 if not r.files and r.files_estimate is not None else str(r.files))
        print(f"{r.name:<50} {r.status:<8} {r.commit or '-':<8} {when:<16} "
              f"{files:>5} arq {r.chunks:>6} chunks{flag}")
        if r.error:
            print(f"    erro: {r.error.splitlines()[0][:150]}")
        if a.cards and r.summary:
            print(f"    {r.summary[:300]}" + (" (cartão desatualizado)" if r.card_stale else ""))
            if r.tags:
                print(f"    tags: {', '.join(r.tags)}")
    c = Counter(r.status for r in rows)
    print(f"\n{len(rows)} repos: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))


def register(sub: Subparsers, settings: Settings) -> None:
    repo = sub.add_parser("repo", help="repositórios").add_subparsers(required=True)
    r = repo.add_parser("add", help="adiciona URL git, org/repo do GitHub ou diretório local")
    r.add_argument("source")
    r.add_argument("--name")
    r.add_argument("--branch")
    r.add_argument("--include", nargs="+", help="substitui os padrões de inclusão (glob)")
    r.add_argument("--exclude", nargs="+", help="padrões extras de exclusão (glob)")
    r.set_defaults(fn=_cmd_repo_add)
    r = repo.add_parser("remove", help="remove repo avulso, ou ignora repo de org")
    r.add_argument("name")
    r.add_argument("--keep-clone", action="store_true")
    r.set_defaults(fn=_cmd_repo_remove)
    r = repo.add_parser("unignore", help="devolve um repo de org ignorado à fila")
    r.add_argument("name")
    r.set_defaults(fn=_cmd_repo_unignore)
    r = repo.add_parser("list", help="estado dos repositórios")
    r.add_argument("filter", nargs="?", help="trecho do nome")
    r.add_argument("-s", "--status", choices=["pending", "indexed", "error", "ignored"])
    r.add_argument("-o", "--org")
    r.add_argument("-c", "--cards", action="store_true", help="mostra o resumo e as tags do cartão")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_list)
    r = repo.add_parser("estimate", help="conta os arquivos indexáveis pela árvore do GitHub (1 chamada por repo; "
                                         "sem custo de embedding)")
    r.add_argument("repo", nargs="*", help="só estes (padrão: os não indexados)")
    r.add_argument("-o", "--org")
    r.add_argument("-n", "--limit", type=int, default=100, help="máximo de chamadas ao GitHub (padrão 100)")
    r.add_argument("--force", action="store_true", help="conta de novo quem já foi contado no mesmo push")
    r.add_argument("--dry-run", action="store_true", help="só diz quantas chamadas faria")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_estimate)
    r = repo.add_parser("find", help="qual repo cuida de um assunto (pelo cartão; sem custo)")
    r.add_argument("query", nargs="+")
    r.add_argument("-n", "--limit", type=int, default=10)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_find)
