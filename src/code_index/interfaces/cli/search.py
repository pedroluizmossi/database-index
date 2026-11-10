"""CLI: `code-index search` (busca híbrida no código e na documentação)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


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
            kind=a.kind,
            include_linked_docs=not a.no_linked_docs,
            exclude_repos=a.exclude_repo,
            exclude_path_glob=a.exclude_path,
            max_docs=a.max_docs,
            domain=a.domain,
            service=a.service,
        )
    if a.json:
        print_json([h.to_dict() for h in hits])
        return
    for h in hits:
        owners = f"  [{', '.join(h.owners)}]" if h.owners else ""
        print(f"\033[1m[{h.score:.3f}] {h.repo}/{h.path}:{h.start_line}-{h.end_line}\033[0m ({h.language or '?'}){owners}")
        if h.also_in:
            print(f"    também em: {', '.join(h.also_in)}")
        if h.url:
            print(f"    {h.title or ''} <{h.url}>" + (f"  ligado a: {', '.join(h.linked_repos)}" if h.linked_repos else ""))
        lines = h.code.splitlines()
        for line in lines[: a.lines]:
            print(f"    {line}")
        if len(lines) > a.lines:
            print(f"    ... (+{len(lines) - a.lines} linhas)")
        print()


def register(sub: Subparsers, settings: Settings) -> None:
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
    q.add_argument("--kind", choices=["code", "doc"], help="só código ou só documentação")
    q.add_argument("--exclude-repo", action="append", help="repo que não pode aparecer (pode repetir)")
    q.add_argument("--exclude-path", action="append", help="glob de caminho que não pode aparecer (pode repetir)")
    q.add_argument("--no-linked-docs", action="store_true",
                   help="com --repo, não inclui as páginas de documentação ligadas ao repo")
    q.add_argument("--max-docs", type=int, help="páginas de documentação no resultado (padrão: top_k/4, mín. 2)")
    q.add_argument("--domain", help="só o código deste domínio do DDD (ex.: platform; ver `owner catalog`)")
    q.add_argument("--service", help="com --domain, só este serviço (ex.: authentication)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=_cmd_search)
