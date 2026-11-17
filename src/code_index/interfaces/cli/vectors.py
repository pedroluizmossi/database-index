"""CLI: `code-index vectors` (vetores dos resumos e perguntas dos arquivos e dos cartões dos repos)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from ...domain.search_vectors import KINDS
from .common import Subparsers, print_json


def _pct(part: int, total: int) -> str:
    return f"{part / total:.1%}" if total else "0%"


def _estimate(e: dict) -> str:
    cost = f", ~US$ {e['cost_usd']:.4f}" if e.get("cost_usd") is not None else ""
    return f"~{e['chars']} caracteres, ~{e['tokens']} tokens{cost}"


async def _cmd_status(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        st = await svc.vector_status()
    if a.json:
        print_json(st)
        return
    print(f"modelo: {st['model']}   última geração: {st['last_embedded_at'] or 'nunca'}\n")
    print(f"{'tipo':<24} {'origem':>7} {'em dia':>7} {'pendentes':>10} {'obsoletos':>10}")
    for kind, c in st["kinds"].items():
        print(f"{c['label']:<24} {c['sources']:>7} {c['embedded']:>7} {c['pending']:>10} {c['obsolete']:>10}")
    cov = st["summary_coverage"]
    print(f"\nresumos: {cov['fresh'] + cov['stale']} de {cov['files']} arquivos de código "
          f"({_pct(cov['fresh'] + cov['stale'], cov['files'])}); {st['stale_summaries']} desatualizado(s) "
          "(votam com peso reduzido até um novo resumo).")
    if st["other_model"]:
        print(f"{st['other_model']} vetor(es) de outro modelo: a busca os ignora até gerar de novo.")
    if st["pending"] or st["obsolete"]:
        print(f"\n{st['pending']} pendente(s) ({_estimate(st['pending_estimate'])}). "
              "Gere com `code-index vectors embed` (custo na Voyage).")


async def _cmd_embed(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        res = await svc.embed_vectors(dry_run=a.dry_run, limit=a.limit, kinds=a.kind)
    if a.json:
        print_json(res)
        return
    if res["dry_run"]:
        print(f"{res['to_embed']} texto(s) seriam embedados ({_estimate(res['estimate'])}) e "
              f"{res['obsolete']} vetor(es) obsoleto(s) apagados. Nada foi gerado.")
        return
    print(f"{res['embedded']} vetor(es) gerado(s), {res['deleted']} apagado(s) em {res['seconds']}s "
          f"({res['embed_tokens']} tokens, US$ {res['cost_usd']:.4f}).")
    if res["pending"] > res["embedded"]:
        print(f"{res['pending'] - res['embedded']} continuam pendentes (--limit).")


def register(sub: Subparsers, settings: Settings) -> None:
    v = sub.add_parser("vectors", help="vetores das perguntas dos resumos e dos cartões dos repos (estado e geração)")
    vs = v.add_subparsers(required=True)
    st = vs.add_parser("status", help="o que está em dia, pendente e obsoleto (sem custo)")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=_cmd_status)
    em = vs.add_parser("embed", help="gera os vetores pendentes e apaga os obsoletos (custo na Voyage)")
    em.add_argument("--dry-run", action="store_true", help="só conta o que seria gerado (sem custo)")
    em.add_argument("--limit", type=int, help="no máximo N textos nesta rodada")
    em.add_argument("--kind", action="append", choices=list(KINDS),
                    help="só este tipo (pode repetir): question, summary, repo_card")
    em.add_argument("--json", action="store_true")
    em.set_defaults(fn=_cmd_embed)
