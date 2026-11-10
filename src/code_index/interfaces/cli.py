"""CLI: code-index {org, repo, sync|index, watch, search, show, enrich, relations, ...}.

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
from ..services import CodeIndexService, open_service
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


async def _cmd_repo_find(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        hits = await svc.find_repos(" ".join(a.query), limit=a.limit)
    if a.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
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
        if a.cards and r.summary:
            print(f"    {r.summary[:300]}" + (" (cartão desatualizado)" if r.card_stale else ""))
            if r.tags:
                print(f"    tags: {', '.join(r.tags)}")
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
    if a.page:
        if len(a.repos) != 1 or not a.repos[0].startswith("docs__"):
            raise SystemExit("--page exige exatamente uma fonte de documentação, ex.: code-index index docs__wiki --page Plataforma/Email")
        if a.dry_run:
            raise SystemExit("--page não combina com --dry-run (use `code-index docs preview` para ver a seleção)")
        async with open_service(settings) as svc:
            res = await svc.index_doc_pages(a.repos[0], a.page)
        _print_result(res)
        return
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
            kind=a.kind,
            include_linked_docs=not a.no_linked_docs,
            exclude_repos=a.exclude_repo,
            exclude_path_glob=a.exclude_path,
            max_docs=a.max_docs,
            domain=a.domain,
            service=a.service,
        )
    if a.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
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


_EVAL_CONFIGS: list[dict] = [
    {"name": "hybrid+rerank", "mode": "hybrid", "rerank": True},
    {"name": "hybrid", "mode": "hybrid", "rerank": False},
    {"name": "semantic", "mode": "semantic", "rerank": False},
    {"name": "keyword", "mode": "keyword", "rerank": False},
]
# Grade da fusão (sem rerank, para isolar o efeito dos pesos): (peso do vetor, peso dos termos)
_EVAL_GRID = [(1.0, 0.2), (1.0, 0.4), (1.0, 0.7), (1.0, 1.0), (0.7, 1.0), (0.4, 1.0)]


async def _cmd_eval(a: argparse.Namespace, settings: Settings) -> None:
    configs = [c for c in _EVAL_CONFIGS if a.no_rerank is False or not c["rerank"]]
    if a.rerank_variants:
        configs = CodeIndexService.rerank_variants()
    elif a.grid:
        configs = [{"name": f"pesos {v}/{k}", "mode": "hybrid", "rerank": False, "weights": (v, k)} for v, k in _EVAL_GRID]
    async with open_service(settings) as svc:
        cases = svc.load_eval_cases(a.file)
        reports = await svc.evaluate(cases, configs, top_k=a.top_k)
    if a.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
        return
    print(f"{len(cases)} perguntas, top {a.top_k}\n")
    print(f"{'configuração':<20} {'hit@1':>6} {'hit@3':>6} {'hit@k':>6} {'MRR':>6}")
    for r in reports:
        m = r.metrics()
        print(f"{r.name:<20} {m['hit@1']:>6.2f} {m['hit@3']:>6.2f} {m['hit@k']:>6.2f} {m['mrr']:>6.2f}")
    tags = reports[0].tags() if reports else []
    if tags:
        counts = {t: sum(1 for c in reports[0].results if c.tag == t) for t in tags}
        print("\nMRR por categoria (nº de perguntas)")
        print(f"{'configuração':<20} " + " ".join(f"{t + f' ({counts[t]})':>11}" for t in tags))
        for r in reports:
            print(f"{r.name:<20} " + " ".join(f"{r.metrics(t)['mrr']:>11.2f}" for t in tags))
    if a.verbose:
        for r in reports:
            print(f"\n\033[1m{r.name}\033[0m")
            for c in r.results:
                mark = f"#{c.rank}" if c.rank else "--"
                print(f"  {mark:>3}  [{c.tag}] {c.query}" + ("" if c.rank else f"   (veio: {', '.join(c.top)})"))


async def _cmd_skips(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.skipped_files(a.repo, limit=a.limit)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
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


def _parse_link(raw: str) -> tuple[str, list[str]]:
    """'Plataforma/email/**=*email*,org__repo' -> ('Plataforma/email/**', ['*email*', 'org__repo'])."""
    path, sep, repos = raw.partition("=")
    if not sep or not path.strip() or not repos.strip():
        raise ValueError(f"--link inválido: {raw!r} (use caminho_glob=repo_glob[,repo_glob...])")
    return path.strip(), [r.strip() for r in repos.split(",") if r.strip()]


async def _cmd_api(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        if a.api_command == "domains":
            catalog = await svc.openapi_catalog()
            for domain in sorted({x["domain"] for x in catalog}):
                print(domain)
        elif a.api_command == "services":
            catalog = await svc.openapi_catalog()
            for row in catalog:
                if row["domain"] == a.domain:
                    print(row["service"])
        else:
            preview = await svc.openapi_preview(a.domain, a.service)
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            if not a.dry_run:
                name = await svc.register_openapi(a.domain, a.service)
                result = await svc.sync([name], batch=None, do_discover=False,
                                        doc_pages=frozenset(a.service) if a.service else None)
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
                if result.errors:
                    raise SystemExit(1)


async def _cmd_api_impl(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rows = await svc.find_api_impl(" ".join(a.operation), service=a.service, domain=a.domain, limit=a.limit)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("nenhum handler encontrado (só operações de .sdl casadas por nome; rode `code-index enrich facts`).")
    for r in rows:
        print(f"\033[1m{r['domain']}/{r['service']} {r['kind']} {r['operation']}\033[0m  → "
              f"{r['repo']}/{r['path']}:{r['line']}  ({r['symbol']}, por {r['origin']})")


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
        docs = svc.list_docs()
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
        print(json.dumps(prev, indent=2, ensure_ascii=False))
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
        print(json.dumps(rows, indent=2, ensure_ascii=False))
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
        print(json.dumps(docs, indent=2, ensure_ascii=False))
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
        print(json.dumps(rows, indent=2, ensure_ascii=False))
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


# ---------------------------------------------------------------- enrich (cartão do repo)

async def _cmd_enrich_facts(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.enrich_facts(a.repo or None, force=a.force)
    print(f"fatos coletados de {st['updated']} repo(s) (de {st['repos']}); sem LLM e sem custo.")


async def _cmd_enrich_next(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        task = await svc.enrich_next(agent=a.agent or "", repo=a.repo, kind=a.kind, scope=a.escopo)
    if task is None:
        print("nenhuma tarefa pendente.")
        return
    if a.json:
        print(json.dumps({k: v for k, v in task.items() if k != "text"}, indent=2, ensure_ascii=False))
        return
    print(task["text"])


async def _cmd_enrich_submit(a: argparse.Namespace, settings: Settings) -> None:
    if a.file:
        with open(a.file, encoding="utf-8") as fh:
            payload = fh.read()
    else:
        payload = sys.stdin.read()
    async with open_service(settings) as svc:
        res = await svc.enrich_submit(a.id, payload, agent=a.agent or "")
    if res["kind"] == "file_summaries":
        print(f"{res['files']} resumo(s) gravado(s) em {res['repo']} ({res['chunks']} trecho(s) atualizado(s) na busca). "
              f"Arquivos na fila: {res['remaining']}.")
        if res["remaining"]:
            print("próxima: uv run code-index enrich next --kind file_summaries")
        return
    if res["kind"] == "doc_links":
        print(f"{res['pages']} página(s) analisada(s), {res['suggested']} sugestão(ões) para revisar "
              f"({res['removals']} remoção(ões) de vínculo automático; code-index docs sugestoes). "
              f"Páginas na fila: {res['remaining']}.")
        if res["remaining"]:
            print("próxima: uv run code-index enrich next --kind doc_links")
        return
    print(f"cartão de {res['repo']} gravado. Pendentes: {res['remaining']}.")
    if res["remaining"]:
        print("próxima: uv run code-index enrich next")


async def _cmd_enrich_apply_summaries(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.apply_file_summaries()
    print(f"{res['chunks']} trecho(s) atualizado(s); {res['pruned']} resumo(s) de arquivos que saíram do índice apagado(s).")


async def _cmd_enrich_release(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        ok = await svc.enrich_release(a.id)
    print("tarefa devolvida à fila." if ok else "tarefa não está reservada (já entregue, expirada ou inexistente).")


async def _cmd_enrich_status(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.enrich_status()
    if a.json:
        print(json.dumps(st, indent=2, ensure_ascii=False, default=str))
        return
    print(f"repos indexados: {st['repos']} | com fatos: {st['with_facts']} (com SDL: {st['with_sdl']})")
    print(f"cartões: {st['cards_ok']} em dia | {st['cards_stale']} desatualizados | {st['cards_missing']} sem cartão")
    print(f"pendentes (sem reserva): {st['pending']} | tarefas: "
          + (", ".join(f"{k} {v}" for k, v in sorted(st["tasks"].items())) or "nenhuma")
          + f" | entregas recusadas: {st['rejections']}")
    d = st.get("docs")
    if d:
        print(f"wiki → repos: {d['pending_pages']} página(s) a analisar ({d['pending_unlinked']} sem vínculo, "
              f"{d['pending_multi']} com vários repos) | analisadas {d['reviewed']}")
        print(f"  sugestões pendentes: {d['suggested_add']} adicionar, {d['suggested_remove']} remover | aprovadas: "
              f"{d['approved_add']} adicionadas, {d['approved_remove']} removidas | rejeitadas {d['rejected']}")
    sm = st.get("summaries")
    if sm and sm["files"]:
        print(f"resumos de arquivos: {sm['fresh']} em dia | {sm['stale']} desatualizados | {sm['missing']} sem resumo "
              f"(de {sm['files']} arquivos de código)")
    imp = st.get("impls")
    if imp:
        print(f"handlers de API (SDL, por nome): {imp['matched']} de {imp['operations']} operações")
    for t in st["leased"]:
        print(f"  reservada {t['id']}  {t['repo']}{(' ' + t['subject']) if t.get('subject') else ''}"
              f"  agent={t['agent'] or '-'}  até {t['lease_until']}")


async def _cmd_enrich_show(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        info = await svc.repo_card(a.repo)
    if info is None:
        print("sem fatos para este repo ainda (rode `code-index enrich facts`).")
        return
    print(json.dumps(info, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- relations

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
        print(json.dumps(st, indent=2, ensure_ascii=False, default=str))
        return
    print("efetivas: " + ", ".join(f"{k} {v}" for k, v in sorted(st["effective"].items())))
    for r in st["rows"]:
        extra = "" if (r["status"], r["action"]) == ("active", "add") else f" [{r['status']}/{r['action']}]"
        print(f"  {r['type']:<15} {r['origin']:<10} {r['n']:>6}{extra}")


async def _cmd_relations_show(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.related(a.entity, type_=a.type, limit=a.limit)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    print(f"\033[1m{res['entity']}\033[0m")
    if not res["relations"]:
        print("  nenhuma relação.")
    for r in res["relations"]:
        arrow = "→" if r["direction"] == "out" else "←"
        print(f"  {arrow} {r['type']:<15} {r['entity']}  ({', '.join(r['origins'])})")


_ORIGIN_LABEL = {"manual": "manual", "declared": "SDL", "sibling": "irmão", "api_calls": "chamadas", "usage": "uso",
                 "none": "sem dono"}


async def _cmd_owner_catalog(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.ownership_catalog()
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
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
        print(json.dumps(rows, indent=2, ensure_ascii=False))
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


async def _cmd_serve(a: argparse.Namespace, settings: Settings) -> None:
    import uvicorn

    from .http.app import create_app, server_config

    host = a.host or settings.http_host
    port = a.port or settings.http_port
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.public_url.startswith("https://"):
        logging.warning("escutando em %s sem CODE_INDEX_PUBLIC_URL https: use um proxy com TLS na frente", host)
    app = create_app(settings)
    await uvicorn.Server(server_config(app, host, port, forwarded_allow_ips=a.forwarded_allow_ips)).serve()


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


async def _cmd_user_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        users = await svc.users_overview(a.days)
    if not users:
        print("nenhum usuário ainda (aparecem no primeiro login ou ao receber um token).")
    print(f"{'usuário':<38} {'papel':<8} {'estado':<10} {'chamadas':>8} {'mcp':>6} {'US$':>8}  último acesso")
    for u in users:
        seen = u["last_seen_at"] or u["last_login_at"]
        last = str(seen)[:16].replace("T", " ") if seen else "nunca"
        state = "ativo" if u["active"] else "desativado"
        print(f"{u['email']:<38} {u['role']:<8} {state:<10} {u['calls']:>8} {u['mcp_calls']:>6} "
              f"{u['cost_usd']:>8.4f}  {last}")
    print(f"(uso nos últimos {a.days} dias)")


async def _cmd_user_access(a: argparse.Namespace, settings: Settings) -> None:
    disable = a.action == "disable"
    async with open_service(settings) as svc:
        res = await svc.set_user_disabled(None, a.email, disable)
        await svc.audit(None, f"user:{a.action}:local", params={"email": a.email})
    print(f"{res['email']}: {'desativado (nenhuma credencial vale)' if disable else 'reativado'}.")


def _fmt_int(n: float | int | None) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


async def _cmd_usage(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rep = await svc.usage_report(a.days, email=a.email)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        return
    t = rep["totals"]
    who = f" de {a.email}" if a.email else ""
    print(f"Uso{who} nos últimos {rep['days']} dias")
    print(f"  chamadas {_fmt_int(t['calls'])} (MCP {_fmt_int(t['mcp_calls'])}), erros {_fmt_int(t['errors'])}, "
          f"usuários {t['users']}, clientes {t['clients']}")
    print(f"  latência média {t['avg_ms']:.0f} ms, p95 {t['p95_ms']:.0f} ms; sem resultado: {_fmt_int(t['empty_results'])}")
    print(f"  Voyage: {_fmt_int(t['embed_tokens'])} tokens de embedding, {_fmt_int(t['rerank_tokens'])} de rerank, "
          f"US$ {t['cost_usd']:.4f}; contexto devolvido a agents (MCP) ~{_fmt_int(t['context_tokens'])} tokens")
    print()
    print(f"  {'canal':<5} {'ação':<40} {'chamadas':>8} {'erros':>6} {'p95 ms':>7} {'~tokens/resp':>12} {'US$':>8}")
    for r in rep["by_action"][:25]:
        print(f"  {r['channel']:<5} {r['action'][:40]:<40} {r['calls']:>8} {r['errors']:>6} {r['p95_ms']:>7.0f} "
              f"{r['avg_context_tokens'] if r['avg_context_tokens'] is not None else '-':>12} {r['cost_usd']:>8.4f}")
    if rep["by_client"]:
        print()
        print("  clientes: " + ", ".join(f"{c['client']} ({c['calls']})" for c in rep["by_client"][:10]))
    if rep["empty_queries"]:
        print()
        print("  consultas sem resultado:")
        for q in rep["empty_queries"][:10]:
            print(f"    {q['calls']:>4}x  {q['action']:<16} {q['query'][:80]}")


def _print_user_settings(info: dict[str, Any]) -> None:
    name = f" ({info['display_name']})" if info["display_name"] else ""
    print(f"{info['email']}{name}")
    changed = set(info["changed"])
    for item in info["schema"]:
        key = item["key"]
        value = json.dumps(info["settings"][key], ensure_ascii=False)
        origin = "alterada" if key in changed else "padrão"
        print(f"  {key:<28} {value:<14} {origin:<9} {item['label']}")


async def _cmd_user_settings(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        _print_user_settings(await svc.user_settings(None, email=a.email))


def _parse_setting(raw: str) -> tuple[str, Any]:
    """'chave=valor'. O valor é lido como JSON (10, true, "dark"); se não for JSON, vale como texto."""
    key, sep, value = raw.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"use chave=valor: {raw}")
    try:
        return key.strip(), json.loads(value)
    except ValueError:
        return key.strip(), value


async def _cmd_user_set(a: argparse.Namespace, settings: Settings) -> None:
    changes = dict(_parse_setting(raw) for raw in a.settings)
    if any(v is None for v in changes.values()):
        raise ValueError("para voltar ao padrão, use 'user unset'")
    async with open_service(settings) as svc:
        info = await svc.update_user_settings(None, changes, email=a.email)
        await svc.audit(None, "settings:update:local", params={"email": a.email, "keys": sorted(changes)})
    _print_user_settings(info)


async def _cmd_user_unset(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        info = await svc.update_user_settings(None, dict.fromkeys(a.keys), email=a.email)
        await svc.audit(None, "settings:update:local", params={"email": a.email, "keys": sorted(a.keys)})
    _print_user_settings(info)


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
    r.add_argument("-c", "--cards", action="store_true", help="mostra o resumo e as tags do cartão")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_list)
    r = repo.add_parser("find", help="qual repo cuida de um assunto (pelo cartão; sem custo)")
    r.add_argument("query", nargs="+")
    r.add_argument("-n", "--limit", type=int, default=10)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_repo_find)

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
    s.add_argument("--page", action="append", metavar="CAMINHO",
                   help="fonte de documentação: indexa só esta página (sem idioma, ex.: Plataforma/Email); "
                        "pode repetir. A página precisa estar na seleção da fonte.")
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

    ev = sub.add_parser("eval", help="mede a qualidade da busca com perguntas de resposta conhecida")
    ev.add_argument("file", nargs="?", default="evals/queries.json")
    ev.add_argument("-k", "--top-k", type=int, default=10)
    ev.add_argument("--no-rerank", action="store_true", help="pula as configurações com rerank (só embeddings)")
    ev.add_argument("--grid", action="store_true", help="compara pesos da fusão vetor/termos (sem rerank)")
    ev.add_argument("--rerank-variants", action="store_true",
                    help="compara as variações do rerank (instrução, cabeçalho, só código) com a busca sem rerank")
    ev.add_argument("-v", "--verbose", action="store_true", help="mostra cada pergunta e o que veio nos erros")
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(fn=_cmd_eval)

    api_cmd = sub.add_parser("api", help="catálogo Swagger no S3").add_subparsers(dest="api_command", required=True)
    api_cmd.add_parser("domains", help="lista domínios sem indexar").set_defaults(fn=_cmd_api)
    ap = api_cmd.add_parser("services", help="lista serviços sem indexar")
    ap.add_argument("--domain", required=True)
    ap.set_defaults(fn=_cmd_api)
    ap = api_cmd.add_parser("impl", help="onde uma operação da API é implementada (handler do .sdl; sem custo)")
    ap.add_argument("operation", nargs="+", help="op, servico.op ou 'POST /servico/queries/op'")
    ap.add_argument("-s", "--service")
    ap.add_argument("-d", "--domain")
    ap.add_argument("-n", "--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.set_defaults(fn=_cmd_api_impl)
    ap = api_cmd.add_parser("index", help="indexa domínio ou serviços selecionados")
    ap.add_argument("--domain", required=True)
    ap.add_argument("--service", action="append", help="serviço exato; pode repetir")
    ap.add_argument("--dry-run", action="store_true")
    ap.set_defaults(fn=_cmd_api)

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

    en = sub.add_parser("enrich", help="enriquecimento offline: cartão do repo feito por agents (next/submit)") \
        .add_subparsers(required=True)
    e = en.add_parser("facts", help="coleta fatos (catalog-info, manifestos, SDL, pastas) sem LLM e sem custo")
    e.add_argument("repo", nargs="*", help="repos (padrão: todos os indexados cuja revisão mudou)")
    e.add_argument("--force", action="store_true", help="recoleta mesmo sem mudança")
    e.set_defaults(fn=_cmd_enrich_facts)
    e = en.add_parser("next", help="reserva a próxima tarefa e imprime instruções + dados para o agent")
    e.add_argument("--kind", choices=["card", "doc_links", "file_summaries"], default="card",
                   help="card: cartão do repo (padrão); doc_links: páginas da wiki sem vínculo → repos; "
                        "file_summaries: lote de arquivos de código para resumir (melhora a busca por termos)")
    e.add_argument("--escopo", choices=["todas", "sem-vinculo", "varios"], default="todas",
                   help="doc_links: todas as páginas (padrão), só as sem vínculo, ou as ligadas a vários repos")
    e.add_argument("--agent", help="nome do agent/ferramenta (registro)")
    e.add_argument("--repo", help="card: repo específico; doc_links: fonte de docs (ex.: docs__wiki)")
    e.add_argument("--json", action="store_true", help="saída estruturada em vez do texto da tarefa")
    e.set_defaults(fn=_cmd_enrich_next)
    e = en.add_parser("submit", help="entrega o cartão (JSON por --file ou entrada padrão)")
    e.add_argument("id")
    e.add_argument("-f", "--file")
    e.add_argument("--agent")
    e.set_defaults(fn=_cmd_enrich_submit)
    e = en.add_parser("apply-summaries",
                      help="reaplica os resumos de arquivos às colunas de busca (sem custo; o sync e o submit já fazem)")
    e.set_defaults(fn=_cmd_enrich_apply_summaries)
    e = en.add_parser("release", help="devolve uma tarefa reservada à fila")
    e.add_argument("id")
    e.set_defaults(fn=_cmd_enrich_release)
    e = en.add_parser("status", help="progresso: fatos, cartões, tarefas reservadas")
    e.add_argument("--json", action="store_true")
    e.set_defaults(fn=_cmd_enrich_status)
    e = en.add_parser("show", help="fatos e cartão de um repo")
    e.add_argument("repo")
    e.set_defaults(fn=_cmd_enrich_show)

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

    # dono no DDD (domínio/serviço)
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
                   help="search:read (padrão), index:write, enrich:write, admin")
    t.add_argument("--days", type=int, help=f"validade em dias (padrão {settings.token_ttl_days})")
    t.set_defaults(fn=_cmd_token_create)
    t = tk.add_parser("list", help="lista tokens de agents")
    t.add_argument("--all", action="store_true", help="inclui revogados/expirados")
    t.set_defaults(fn=_cmd_token_list)
    t = tk.add_parser("revoke", help="revoga um token pelo id")
    t.add_argument("id")
    t.set_defaults(fn=_cmd_token_revoke)

    # usuários e preferências (operador local)
    us = sub.add_parser("user", help="usuários: uso, acesso e configurações").add_subparsers(required=True)
    u = us.add_parser("list", help="lista os usuários, com papel, estado e uso no período")
    u.add_argument("--days", type=int, default=30)
    u.set_defaults(fn=_cmd_user_list)
    for action, text in (("disable", "desativa o usuário (nenhuma credencial dele vale)"), ("enable", "reativa o usuário")):
        u = us.add_parser(action, help=text)
        u.add_argument("email")
        u.set_defaults(fn=_cmd_user_access, action=action)
    u = us.add_parser("settings", help="configurações de um usuário (valor atual e se foi alterada)")
    u.add_argument("email")
    u.set_defaults(fn=_cmd_user_settings)
    u = us.add_parser("set", help="altera configurações de um usuário")
    u.add_argument("email")
    u.add_argument("settings", nargs="+", metavar="chave=valor", help="ex.: ui.theme=dark ui.results_per_page=16")
    u.set_defaults(fn=_cmd_user_set)
    u = us.add_parser("unset", help="volta configurações de um usuário ao padrão")
    u.add_argument("email")
    u.add_argument("keys", nargs="+", metavar="chave")
    u.set_defaults(fn=_cmd_user_unset)

    # uso da aplicação (MCP, API, interface)
    ug = sub.add_parser("usage", help="relatório de uso: tools, clientes, custo da Voyage, consultas sem resultado")
    ug.add_argument("--days", type=int, default=30)
    ug.add_argument("--email", help="só o uso deste usuário")
    ug.add_argument("--json", action="store_true", help="relatório completo em JSON")
    ug.set_defaults(fn=_cmd_usage)

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
