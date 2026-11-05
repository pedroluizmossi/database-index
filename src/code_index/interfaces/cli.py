"""CLI: code-index {org, repo, sync|index, watch, search, show}.

Só parsing e formatação: as regras de negócio estão em `services.CodeIndexService`.
Cada comando roda em UM `asyncio.run` dentro de `open_service()`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from collections.abc import Callable, Coroutine
from typing import Any

from ..domain.models import SyncResult
from ..services import open_service
from ..settings import Settings, bootstrap

CommandFn = Callable[[argparse.Namespace, Settings], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------- org

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


# ---------------------------------------------------------------- repo

async def _cmd_repo_add(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
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
    async with open_service(settings) as svc:
        result = await svc.remove_repo(a.name, keep_clone=a.keep_clone)
    if result == "removed":
        print(f"removido: {a.name}")
    else:
        print(f"{a.name} é de uma org: marcado como ignorado (desfaça com `code-index repo unignore`)")
    print("os chunks somem no próximo `code-index sync`.")


async def _cmd_repo_unignore(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        await svc.unignore_repo(a.name)
    print(f"{a.name} volta à fila de indexação.")


async def _cmd_repo_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.list_repos(status=a.status, org=a.org, name=a.filter)
    if a.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2, ensure_ascii=False))
        return
    if not rows:
        print("nenhum repositório.")
    for r in rows:
        flag = " *desatualizado*" if r.outdated else ""
        when = (r.indexed_at or "")[:16].replace("T", " ")
        print(f"{r.name:<50} {r.status:<8} {r.commit or '-':<8} {when:<16} "
              f"{r.files:>5} arq {r.chunks:>6} chunks{flag}")
        if r.error:
            print(f"    erro: {r.error.splitlines()[0][:150]}")
    c = Counter(r.status for r in rows)
    print(f"\n{len(rows)} repos: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))


# ---------------------------------------------------------------- sync / index

def _print_result(res: SyncResult) -> None:
    for name, commit in res.indexed.items():
        print(f"ok    {name} @ {(commit or '-')[:7]}")
    for name, err in res.errors.items():
        print(f"ERRO  {name}: {err.splitlines()[0][:200]}", file=sys.stderr)
    if res.remaining:
        print(f"{res.remaining} repos ainda na fila (rode de novo ou use `code-index watch`).")


async def _cmd_sync(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.sync(
            a.repos or None,
            batch=None if a.all else a.batch,
            do_discover=not a.no_discover,
            pull=not a.no_pull,
            full=a.full,
            dry_run=a.dry_run,
        )
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


async def _cmd_watch(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        while True:
            try:
                res = await svc.sync(batch=a.batch, progress=False)
                logging.info("lote: %d indexados, %d erros, %d na fila",
                             len(res.indexed), len(res.errors), res.remaining)
            except Exception:
                logging.exception("falha no ciclo de sync")
            await asyncio.sleep(a.interval)


# ---------------------------------------------------------------- consulta

async def _cmd_search(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        hits = await svc.search(
            " ".join(a.query),
            top_k=a.top_k,
            repos=a.repo,
            language=a.lang,
            path_glob=a.path,
            mode=a.mode,
            rerank=not a.no_rerank,
            candidates=a.candidates,
        )
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


async def _cmd_symbol(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        hits = await svc.find_symbol(a.name, kind=a.kind, repos=a.repo, language=a.lang, limit=a.limit)
    if a.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
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
        print(json.dumps({"matches": [m.to_dict() for m in res.matches], "truncated": res.truncated},
                         indent=2, ensure_ascii=False))
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


async def _cmd_serve(a: argparse.Namespace, settings: Settings) -> None:
    import uvicorn

    from .http.app import create_app

    host = a.host or settings.http_host
    port = a.port or settings.http_port
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.public_url.startswith("https://"):
        logging.warning("escutando em %s sem CODE_INDEX_PUBLIC_URL https: use um proxy com TLS na frente", host)
    app = create_app(settings)
    config = uvicorn.Config(
        app, host=host, port=port, log_level="info", proxy_headers=True,
        forwarded_allow_ips=a.forwarded_allow_ips, server_header=False,
    )
    await uvicorn.Server(config).serve()


async def _cmd_token_create(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        token, info = await svc.create_agent_token(
            None, name=a.name, scopes=a.scopes, ttl_days=a.days, owner_email=a.owner,
        )
        await svc.audit(None, "token:create:local", params={"name": a.name, "owner": a.owner, "scopes": a.scopes})
    print(f"token criado: {info.name} (id {info.id}, scopes {', '.join(info.scopes)}, expira {info.expires_at:%Y-%m-%d})")
    print(f"\n    {token}\n")
    print("guarde agora: ele não será mostrado de novo.")


async def _cmd_token_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        tokens = await svc.list_tokens(None, include_inactive=a.all)
    if not tokens:
        print("nenhum token.")
    for t in tokens:
        state = "ativo" if t.active else ("revogado" if t.revoked_at else "expirado")
        used = f"{t.last_used_at:%Y-%m-%d %H:%M}" if t.last_used_at else "nunca"
        exp = f"{t.expires_at:%Y-%m-%d}" if t.expires_at else "-"
        print(f"{t.id}  {t.token_prefix}…  {state:<8} {t.name:<30} {t.owner_email:<30} "
              f"{','.join(t.scopes):<30} expira {exp}  último uso {used}")


async def _cmd_token_revoke(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        await svc.revoke_token(None, a.id)
        await svc.audit(None, "token:revoke:local", params={"id": a.id})
    print(f"token {a.id} revogado.")


def main() -> None:
    settings = bootstrap()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="code-index", description="Indexador de código com CocoIndex + Voyage + pgvector")
    sub = p.add_subparsers(required=True)

    # org
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
    s.add_argument("-b", "--batch", type=int, default=settings.default_batch,
                   help=f"máximo de repos por execução no lote automático (padrão {settings.default_batch})")
    s.add_argument("--all", action="store_true", help="sem limite de lote")
    s.add_argument("--no-discover", action="store_true", help="não consulta a API do GitHub")
    s.add_argument("--no-pull", action="store_true", help="não atualiza os clones")
    s.add_argument("--full", action="store_true", help="reprocessa tudo, ignorando caches (caro!)")
    s.add_argument("--dry-run", action="store_true", help="só mostra o que seria processado (sem custo)")
    s.set_defaults(fn=_cmd_sync)

    w = sub.add_parser("watch", help="sync periódico em lotes")
    w.add_argument("-b", "--batch", type=int, default=settings.default_batch)
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

    sy = sub.add_parser("symbol", help="onde uma classe/função/método é declarado")
    sy.add_argument("name", help="nome, parte dele ou Classe.metodo")
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

    # servidor HTTP (API + MCP)
    sv = sub.add_parser("serve", help="sobe a API HTTP e o MCP (/mcp)")
    sv.add_argument("--host", help=f"padrão CODE_INDEX_HTTP_HOST ({settings.http_host})")
    sv.add_argument("--port", type=int, help=f"padrão CODE_INDEX_HTTP_PORT ({settings.http_port})")
    sv.add_argument("--forwarded-allow-ips", default="127.0.0.1",
                    help="IPs do proxy reverso confiáveis para X-Forwarded-* (padrão 127.0.0.1)")
    sv.set_defaults(fn=_cmd_serve)

    # tokens (operador local, com acesso direto ao banco)
    tk = sub.add_parser("token", help="tokens de agents (operador local)").add_subparsers(required=True)
    t = tk.add_parser("create", help="emite um token (valor exibido uma única vez)")
    t.add_argument("name", help="rótulo, ex.: 'claude-code pedro'")
    t.add_argument("--owner", required=True, help="e-mail do responsável")
    t.add_argument("--scopes", nargs="+", default=["search:read"],
                   help="search:read (padrão), index:write, admin")
    t.add_argument("--days", type=int, help=f"validade em dias (padrão {settings.token_ttl_days})")
    t.set_defaults(fn=_cmd_token_create)
    t = tk.add_parser("list", help="lista tokens de agents")
    t.add_argument("--all", action="store_true", help="inclui revogados/expirados")
    t.set_defaults(fn=_cmd_token_list)
    t = tk.add_parser("revoke", help="revoga um token pelo id")
    t.add_argument("id")
    t.set_defaults(fn=_cmd_token_revoke)

    a = p.parse_args()
    fn: CommandFn = a.fn
    try:
        asyncio.run(fn(a, settings))
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, PermissionError) as e:
        sys.exit(f"erro: {e}")


if __name__ == "__main__":
    main()
