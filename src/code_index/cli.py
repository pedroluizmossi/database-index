"""CLI: code-index {org, repo, sync|index, watch, search, show}."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter

from . import config
from .config import Org, Repo, load_orgs, load_repos, name_from_url, normalize_source, save_config


def _run_state(fn):
    """Executa fn(conn) numa conexão com a tabela de estado."""
    from . import state

    async def run():
        conn = await state.connect()
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(run())


# ---------------------------------------------------------------- org

def _cmd_org_add(a: argparse.Namespace) -> None:
    from .indexer import discover

    orgs = load_orgs()
    if any(o.name == a.name for o in orgs):
        sys.exit(f"org já configurada: {a.name} (edite o repos.toml para mudar filtros)")
    kw: dict = {}
    if a.match:
        kw["match"] = tuple(a.match)
    if a.skip:
        kw["skip"] = tuple(a.skip)
    if a.languages:
        kw["languages"] = tuple(a.languages)
    if a.exclude:
        kw["extra_exclude"] = tuple(a.exclude)
    org = Org(name=a.name, include_archived=a.include_archived, include_forks=a.include_forks,
              max_size_mb=a.max_size_mb, **kw)
    stats = discover([org])[org.name]  # valida acesso antes de salvar
    save_config(orgs=orgs + [org])
    print(f"org {a.name}: {stats['total']} repositórios descobertos (pendentes de indexação).")
    print("indexe aos poucos com `code-index sync --batch N` ou um a um com `code-index index <nome>`.")


def _cmd_org_set(a: argparse.Namespace) -> None:
    from dataclasses import replace

    from .indexer import discover

    orgs = load_orgs()
    org = next((o for o in orgs if o.name == a.name), None)
    if org is None:
        sys.exit(f"org não configurada: {a.name}")
    changes: dict = {}
    for key in ("languages", "match", "skip"):
        v = getattr(a, key)
        if v is not None:
            changes[key] = tuple(x for x in v if x)  # `--languages ""` limpa o filtro
    if a.max_size_mb is not None:
        changes["max_size_mb"] = a.max_size_mb or None  # 0 remove o limite
    if not changes:
        sys.exit("nada a alterar (use --languages, --match, --skip ou --max-size-mb)")
    new = replace(org, **changes)
    save_config(orgs=[new if o.name == a.name else o for o in orgs])
    st = discover([new])[new.name]
    print(f"org {a.name}: {st['total']} repos ({st['new']} novos, {st['removed']} removidos pelo filtro).")
    if st["removed"]:
        print("os chunks dos repos removidos somem no próximo `code-index sync`.")


def _cmd_org_remove(a: argparse.Namespace) -> None:
    from . import state
    from .git_sync import remove_clone

    orgs = load_orgs()
    if not any(o.name == a.name for o in orgs):
        sys.exit(f"org não configurada: {a.name}")
    save_config(orgs=[o for o in orgs if o.name != a.name])
    names = _run_state(lambda c: state.prune_orgs(c, [o.name for o in orgs if o.name != a.name]))
    if not a.keep_clones:
        for n in names:
            remove_clone(Repo(name=n, url="-"))
    print(f"org {a.name} removida ({len(names)} repos). Os chunks somem no próximo `code-index sync`.")


def _cmd_org_refresh(a: argparse.Namespace) -> None:
    from .indexer import discover

    orgs = [o for o in load_orgs() if not a.name or o.name == a.name]
    if not orgs:
        sys.exit("nenhuma org configurada" + (f" com nome {a.name}" if a.name else ""))
    for name, st in discover(orgs).items():
        print(f"{name}: {st['total']} repos ({st['new']} novos, {st['removed']} removidos)")


def _cmd_org_list(a: argparse.Namespace) -> None:
    from .search import repo_status

    rows = asyncio.run(repo_status())
    for org in load_orgs():
        mine = [r for r in rows if r["org"] == org.name]
        c = Counter(r["status"] for r in mine)
        outdated = sum(r["outdated"] for r in mine)
        filters = ", ".join(f"{k}={v}" for k, v in (("languages", list(org.languages)), ("match", list(org.match)),
                                                     ("skip", list(org.skip)), ("max_size_mb", org.max_size_mb)) if v)
        print(f"{org.name:<25} {len(mine):>4} repos | indexed {c['indexed']:>4} | pending {c['pending']:>4} | "
              f"error {c['error']:>3} | ignored {c['ignored']:>3} | desatualizados {outdated:>3}"
              + (f"  [{filters}]" if filters else ""))
        langs = Counter(r["language"] or "none" for r in mine)
        print("    linguagens: " + ", ".join(f"{k} {v}" for k, v in langs.most_common()))


# ---------------------------------------------------------------- repo

def _cmd_repo_add(a: argparse.Namespace) -> None:
    url, path = normalize_source(a.source)
    name = a.name or (name_from_url(url) if url else config.pathlib.Path(path).name)
    repos = load_repos()
    if any(r.name == name for r in repos):
        sys.exit(f"já existe um repo chamado {name!r}")
    kw = {}
    if a.include:
        kw["include"] = tuple(a.include)
    if a.exclude:
        kw["extra_exclude"] = tuple(a.exclude)
    repos.append(Repo(name=name, url=url, path=path, branch=a.branch, **kw))
    save_config(repos=repos)
    print(f"adicionado: {name} -> {url or path}")
    print(f"rode `code-index index {name}` para clonar e indexar.")


def _cmd_repo_remove(a: argparse.Namespace) -> None:
    from . import state
    from .git_sync import remove_clone

    repos = load_repos()
    manual = next((r for r in repos if r.name == a.name), None)
    if manual is not None:
        save_config(repos=[r for r in repos if r.name != a.name])
        _run_state(lambda c: state.delete(c, [a.name]))
        if not a.keep_clone:
            remove_clone(manual)
        print(f"removido: {a.name}")
    elif _run_state(lambda c: state.set_ignored(c, a.name, True)):
        print(f"{a.name} é de uma org: marcado como ignorado (desfaça com `code-index repo unignore`)")
    else:
        sys.exit(f"repo não encontrado: {a.name}")
    print("os chunks somem no próximo `code-index sync`.")


def _cmd_repo_unignore(a: argparse.Namespace) -> None:
    from . import state

    if not _run_state(lambda c: state.set_ignored(c, a.name, False)):
        sys.exit(f"repo não encontrado: {a.name}")
    print(f"{a.name} volta à fila de indexação.")


def _cmd_repo_list(a: argparse.Namespace) -> None:
    from .search import repo_status

    rows = asyncio.run(repo_status(status=a.status, org=a.org, name=a.filter))
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("nenhum repositório.")
    for r in rows:
        flag = " *desatualizado*" if r["outdated"] else ""
        when = (r["indexed_at"] or "")[:16].replace("T", " ")
        print(f"{r['name']:<50} {r['status']:<8} {r['commit'] or '-':<8} {when:<16} "
              f"{r['files']:>5} arq {r['chunks']:>6} chunks{flag}")
        if r["error"]:
            print(f"    erro: {r['error'].splitlines()[0][:150]}")
    c = Counter(r["status"] for r in rows)
    print(f"\n{len(rows)} repos: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))


# ---------------------------------------------------------------- sync / index

def _print_result(res) -> None:
    for name, commit in res.indexed.items():
        print(f"ok    {name} @ {(commit or '-')[:7]}")
    for name, err in res.errors.items():
        print(f"ERRO  {name}: {err.splitlines()[0][:200]}", file=sys.stderr)
    if res.remaining:
        print(f"{res.remaining} repos ainda na fila (rode de novo ou use `code-index watch`).")


def _cmd_sync(a: argparse.Namespace) -> None:
    from .indexer import sync

    res = sync(a.repos or None, batch=None if a.all else a.batch, do_discover=not a.no_discover,
               pull=not a.no_pull, full=a.full, dry_run=a.dry_run)
    if a.dry_run:
        print("seria processado:" if res.selected else "nada a processar.")
        for name in res.selected:
            print(f"  {name}")
        if res.remaining:
            print(f"(+{res.remaining} na fila depois deste lote)")
        return
    _print_result(res)
    if res.errors and a.repos:
        sys.exit(1)


def _cmd_watch(a: argparse.Namespace) -> None:
    from .indexer import sync

    while True:
        try:
            res = sync(batch=a.batch, progress=False)
            logging.info("lote: %d indexados, %d erros, %d na fila",
                         len(res.indexed), len(res.errors), res.remaining)
        except Exception:
            logging.exception("falha no ciclo de sync")
        time.sleep(a.interval)


# ---------------------------------------------------------------- consulta

def _cmd_search(a: argparse.Namespace) -> None:
    from .embedding import close_litellm_clients
    from .search import search

    async def run():
        try:
            return await search(" ".join(a.query), top_k=a.top_k, repos=a.repo,
                                language=a.lang, path_glob=a.path, mode=a.mode,
                                rerank=not a.no_rerank, candidates=a.candidates)
        finally:
            await close_litellm_clients()

    hits = asyncio.run(run())
    if a.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
        return
    for h in hits:
        print(f"\033[1m[{h.score:.3f}] {h.repo}/{h.path}:{h.start_line}-{h.end_line}\033[0m ({h.language or '?'})")
        lines = h.code.splitlines()
        for line in lines[: a.lines]:
            print(f"    {line}")
        if len(lines) > a.lines:
            print(f"    ... (+{len(lines) - a.lines} linhas)")
        print()


def _cmd_show(a: argparse.Namespace) -> None:
    from .search import read_file

    repo, _, path = a.target.partition("/")
    rng = None
    if ":" in path:
        path, rng = path.rsplit(":", 1)
    start = end = None
    if rng:
        s, _, e = rng.partition("-")
        start, end = int(s), int(e or s)
    print(read_file(repo, path, start, end))


def main() -> None:
    from .indexer import DEFAULT_BATCH

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="code-index", description="Indexador de código com CocoIndex + Voyage + pgvector")
    sub = p.add_subparsers(required=True)

    # org
    org = sub.add_parser("org", help="orgs do GitHub (descoberta automática de repos)").add_subparsers(required=True)
    o = org.add_parser("add", help="adiciona uma org e descobre seus repos (não indexa ainda)")
    o.add_argument("name")
    o.add_argument("--match", nargs="+", help="só repos cujo nome casa com estes globs (ex.: 'snr-*')")
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

    # repo
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
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_list)

    # sync / index
    s = sub.add_parser("sync", aliases=["index"],
                       help="indexa os repos informados, ou um lote dos pendentes/alterados")
    s.add_argument("repos", nargs="*",
                   help="nomes (org__repo, org/repo ou só repo), URL git ou diretório; vazio = lote automático")
    s.add_argument("-b", "--batch", type=int, default=DEFAULT_BATCH,
                   help=f"máximo de repos por execução no lote automático (padrão {DEFAULT_BATCH})")
    s.add_argument("--all", action="store_true", help="sem limite de lote")
    s.add_argument("--no-discover", action="store_true", help="não consulta a API do GitHub")
    s.add_argument("--no-pull", action="store_true", help="não atualiza os clones")
    s.add_argument("--full", action="store_true", help="reprocessa tudo, ignorando caches (caro!)")
    s.add_argument("--dry-run", action="store_true", help="só mostra o que seria processado (sem custo)")
    s.set_defaults(fn=_cmd_sync)

    w = sub.add_parser("watch", help="sync periódico em lotes")
    w.add_argument("-b", "--batch", type=int, default=DEFAULT_BATCH)
    w.add_argument("--interval", type=int, default=300, help="segundos entre lotes (padrão 300)")
    w.set_defaults(fn=_cmd_watch)

    # consulta
    q = sub.add_parser("search", help="busca híbrida no código")
    q.add_argument("query", nargs="+")
    q.add_argument("-k", "--top-k", type=int, default=8)
    q.add_argument("-r", "--repo", action="append", help="filtra por repo (pode repetir)")
    q.add_argument("-l", "--lang", help="filtra por linguagem (ex.: java)")
    q.add_argument("-p", "--path", help="filtra por glob de caminho (ex.: 'src/**/*.java')")
    q.add_argument("-m", "--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    q.add_argument("-n", "--lines", type=int, default=12, help="linhas exibidas por resultado")
    q.add_argument("--no-rerank", action="store_true", help="só busca híbrida, sem reranker")
    q.add_argument("-c", "--candidates", type=int, help="candidatos enviados ao reranker (padrão RERANK_CANDIDATES=40)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=_cmd_search)

    v = sub.add_parser("show", help="mostra arquivo: repo/caminho[:inicio-fim]")
    v.add_argument("target")
    v.set_defaults(fn=_cmd_show)

    a = p.parse_args()
    try:
        a.fn(a)
    except (ValueError, RuntimeError) as e:
        sys.exit(f"erro: {e}")


if __name__ == "__main__":
    main()
