"""CLI: `code-index enrich` (enriquecimento offline: cartões, vínculos da wiki e resumos feitos por agents)."""

from __future__ import annotations

import argparse
import sys

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


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
        print_json({k: v for k, v in task.items() if k != "text"})
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
        print_json(st, default=str)
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
    print_json(info)


def register(sub: Subparsers, settings: Settings) -> None:
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
