"""CLI: `code-index docs` (fontes de documentação externa, vínculos página → repo e sugestões)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


def _parse_link(raw: str) -> tuple[str, list[str]]:
    """'Plataforma/email/**=*email*,org__repo' -> ('Plataforma/email/**', ['*email*', 'org__repo'])."""
    path, sep, repos = raw.partition("=")
    if not sep or not path.strip() or not repos.strip():
        raise ValueError(f"--link inválido: {raw!r} (use caminho_glob=repo_glob[,repo_glob...])")
    return path.strip(), [r.strip() for r in repos.split(",") if r.strip()]


async def _cmd_docs_add(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.add_docs(
            a.name, url=a.url, locale=a.locale, include=a.include or (), exclude=a.exclude or (),
            links=[_parse_link(x) for x in (a.link or [])],
        )
    print(f"fonte {res['name']}: {res['pages']} páginas selecionadas (de {res['pages_in_locale']} em {a.locale}).")
    print(f"nenhuma página foi salva ou indexada: rode `code-index index {res['name']}` (ou indexe pela interface).")


async def _cmd_docs_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        docs = await svc.list_docs()
    if not docs:
        print("nenhuma fonte de documentação.")
    for d in docs:
        print(f"{d.repo_name:<30} {d.type:<7} {d.url}  locale={d.locale}"
              + (f"  include={list(d.include)}" if d.include else "") + (f"  exclude={list(d.exclude)}" if d.exclude else ""))
        for rule in d.links:
            print(f"    vínculo: {rule.path} -> {', '.join(rule.repos)}")


async def _cmd_docs_preview(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        prev = await svc.docs_preview(a.name, limit=a.limit)
    if a.json:
        print_json(prev)
        return
    print(f"{prev['name']}: {prev['total']} páginas de {prev['pages_in_locale']} em {prev['locale']} ({prev['url']})")
    for r in prev["roots"]:
        print(f"  {r['root']:<34} {r['pages']:>5}")
    print()
    for p in prev["pages"]:
        print(f"  {p['title']}  \033[2m{p['path']}\033[0m")
    if prev["truncated"]:
        print(f"  ... (mostrando {len(prev['pages'])} de {prev['total']}; use --limit)")


async def _cmd_docs_link(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.link_doc(a.source, a.page, a.repo, reason=a.reason or "", origin=a.origin)
    estado = ("página indexada: a busca já usa o vínculo." if res["indexed"]
              else "página ainda não indexada: o vínculo vale quando ela entrar no índice.")
    print(f"vínculo gravado ({res['origin']}): {res['page']} -> {res['repo']}; {estado}")


async def _cmd_docs_ignore(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.ignore_doc(a.source, a.page, reason=a.reason or "")
    print(f"página ignorada: {res['page']} (sai dos resultados da busca; `docs unignore` desfaz).")


async def _cmd_docs_unignore(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        removed = await svc.unignore_doc(a.source, a.page)
    print("página voltou para a busca." if removed else "a página não estava ignorada.")


async def _cmd_docs_unlink(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        removed = await svc.unlink_doc(a.source, a.page, a.repo)
    print("vínculo removido." if removed else "nenhum vínculo para remover.")


async def _cmd_docs_vinculos(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.doc_links(source=a.source, repo=a.repo)
    if a.json:
        print_json(rows)
        return
    if not rows:
        print("nenhum vínculo gravado.")
    for r in rows:
        print(f"{r['doc_repo']}/{r['doc_path']} -> {r['repo']}  [{r['origin']}]  {r['created_at'][:10]}")
        if r["reason"]:
            print(f"    {r['reason']}")


async def _cmd_docs_links(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        docs = await svc.linked_docs(a.repo, limit=a.limit)
    if a.json:
        print_json(docs)
        return
    if not docs:
        print("nenhuma página ligada (os vínculos aparecem depois do sync da fonte de documentação e do repo).")
    for d in docs:
        print(f"\033[1m{d['title'] or d['path']}\033[0m  [{', '.join(d['origins'])}]")
        print(f"    {d['url'] or d['doc_repo'] + '/' + d['path']}")


async def _cmd_docs_suggestions(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.doc_suggestions(status=a.status, source=a.source, confidence=a.confianca,
                                         action=a.acao, limit=a.limit)
    if a.json:
        print_json(rows)
        return
    if not rows:
        print("nenhuma sugestão.")
    for r in rows:
        verb = "REMOVER" if r["action"] == "remove" else "adicionar"
        print(f"[{r['confidence'] or '-'}] {verb} {r['doc_repo']}/{r['doc_path']}  →  \033[1m{r['repo']}\033[0m"
              + (f"  ({r['basis']})" if r["basis"] else ""))
        print(f"    {r['title'] or ''} — {r['reason']}")
    if rows and a.status == "suggested":
        print("\naprovar: code-index docs aprovar <doc_repo> <caminho> <repo>   (ou --confianca alta para o lote)")


async def _cmd_docs_review(a: argparse.Namespace, settings: Settings) -> None:
    approve = a.docs_review == "aprovar"
    async with open_service(settings) as svc:
        if approve and a.confianca:
            n = await svc.approve_doc_suggestions(confidence=a.confianca, source=a.source, action=a.acao)
            print(f"{n} sugestão(ões) de confiança {a.confianca} aprovada(s).")
            return
        if not (a.doc_repo and a.doc_path and a.repo):
            raise ValueError("informe <doc_repo> <caminho> <repo>" + (" ou --confianca" if approve else ""))
        ok = await svc.review_doc_suggestion(a.doc_repo, a.doc_path, a.repo, approve=approve)
    print(("aprovada." if approve else "rejeitada.") if ok else "sugestão não encontrada (ou já revisada).")


async def _cmd_docs_remove(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        await svc.remove_docs(a.name, keep_files=a.keep_files)
    print(f"fonte {a.name} removida; os trechos somem no próximo sync.")


def register(sub: Subparsers, settings: Settings) -> None:
    dc = sub.add_parser("docs", help="fontes de documentação externa (Wiki.js)").add_subparsers(required=True)
    d = dc.add_parser("add", help="cadastra uma wiki (valida o acesso; não indexa)")
    d.add_argument("name", help="nome curto; o repo de documentação vira docs__<nome>")
    d.add_argument("--url", required=True, help="base da wiki, ex.: https://wiki.exemplo.com")
    d.add_argument("--locale", default="pt-br")
    d.add_argument("--include", nargs="+", help="globs de caminho de página (ex.: 'Plataforma/**')")
    d.add_argument("--exclude", nargs="+", help="globs de caminho de página a ignorar")
    d.add_argument("--link", action="append",
                   help="vínculo explícito: 'caminho_glob=repo_glob[,repo_glob]' (pode repetir)")
    d.set_defaults(fn=_cmd_docs_add)
    dc.add_parser("list", help="fontes cadastradas").set_defaults(fn=_cmd_docs_list)
    d = dc.add_parser("preview", help="páginas que entrariam na indexação, por raiz (sem custo)")
    d.add_argument("name")
    d.add_argument("--limit", type=int, default=30, help="páginas listadas (máx. 1000)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_cmd_docs_preview)
    d = dc.add_parser("link", help="grava vínculo página → repo (sem reindexar)")
    d.add_argument("source", help="nome da fonte de documentação, ex.: wiki")
    d.add_argument("page", help="caminho da página sem idioma, ex.: Plataforma/Email")
    d.add_argument("repo")
    d.add_argument("--reason", help="motivo da ligação (obrigatório para origem agent)")
    d.add_argument("--origin", choices=["manual", "agent"], default="manual")
    d.set_defaults(fn=_cmd_docs_link)
    d = dc.add_parser("ignore", help="tira uma página dos resultados da busca (reversível)")
    d.add_argument("source")
    d.add_argument("page", help="caminho da página sem idioma, ex.: SRE/post-mortems/antigo")
    d.add_argument("--reason", help="motivo (opcional)")
    d.set_defaults(fn=_cmd_docs_ignore)
    d = dc.add_parser("unignore", help="volta a mostrar uma página ignorada")
    d.add_argument("source")
    d.add_argument("page")
    d.set_defaults(fn=_cmd_docs_unignore)
    d = dc.add_parser("unlink", help="remove um vínculo gravado")
    d.add_argument("source")
    d.add_argument("page")
    d.add_argument("repo")
    d.set_defaults(fn=_cmd_docs_unlink)
    d = dc.add_parser("vinculos", help="vínculos gravados manualmente ou por agent")
    d.add_argument("--source", help="filtra por fonte de documentação")
    d.add_argument("--repo", help="filtra por repo")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_cmd_docs_vinculos)
    d = dc.add_parser("links", help="páginas de documentação ligadas a um repo (e por quê)")
    d.add_argument("repo")
    d.add_argument("--limit", type=int, default=100)
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_cmd_docs_links)
    d = dc.add_parser("sugestoes", help="vínculos página → repo sugeridos por LLM, para revisar")
    d.add_argument("--status", choices=["suggested", "active", "rejected"], default="suggested")
    d.add_argument("--source", help="fonte (ex.: wiki ou docs__wiki)")
    d.add_argument("--confianca", choices=["alta", "media", "baixa"])
    d.add_argument("--acao", choices=["add", "remove"], help="add = novos vínculos; remove = remoções")
    d.add_argument("-n", "--limit", type=int, default=200)
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_cmd_docs_suggestions)
    for name, help_ in (("aprovar", "aprova sugestão (o vínculo passa a valer)"),
                        ("rejeitar", "rejeita sugestão (não volta a ser sugerida)")):
        d = dc.add_parser(name, help=help_)
        d.add_argument("doc_repo", nargs="?")
        d.add_argument("doc_path", nargs="?")
        d.add_argument("repo", nargs="?")
        if name == "aprovar":
            d.add_argument("--confianca", choices=["alta", "media", "baixa"], help="aprova todas desta confiança")
            d.add_argument("--acao", choices=["add", "remove"], help="com --confianca: só esta ação")
            d.add_argument("--source")
        else:
            d.set_defaults(confianca=None, source=None, acao=None)
        d.set_defaults(fn=_cmd_docs_review, docs_review=name)
    d = dc.add_parser("remove", help="remove uma fonte")
    d.add_argument("name")
    d.add_argument("--keep-files", action="store_true", help="mantém as páginas baixadas")
    d.set_defaults(fn=_cmd_docs_remove)
