"""CLI: `code-index eval` (qualidade da busca com perguntas de resposta conhecida)."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from ...services import CodeIndexService, open_service
from ...settings import Settings
from .common import Subparsers, print_json

if TYPE_CHECKING:
    from ...retrieval.evaluation import ConfigDiff, ConfigReport

_EVAL_CONFIGS: list[dict] = [
    {"name": "hybrid+rerank", "mode": "hybrid", "rerank": True},
    {"name": "hybrid", "mode": "hybrid", "rerank": False},
    {"name": "semantic", "mode": "semantic", "rerank": False},
    {"name": "keyword", "mode": "keyword", "rerank": False},
]
# Grade da fusão (sem rerank, para isolar o efeito dos pesos): (peso do vetor, peso dos termos)
_EVAL_GRID = [(1.0, 0.2), (1.0, 0.4), (1.0, 0.7), (1.0, 1.0), (0.7, 1.0), (0.4, 1.0)]
_SPLITS = ("dev", "test")
# --extras: o efeito dos vetores de resumos, perguntas e cartões, e do peso do resumo desatualizado (com rerank)
_EXTRAS_CONFIGS: list[dict] = [
    {"name": "hybrid+rerank", "mode": "hybrid", "rerank": True},
    {"name": "-extras", "mode": "hybrid", "rerank": True, "extra_weights": (0.0, 0.0)},
    {"name": "só perguntas", "mode": "hybrid", "rerank": True, "extra_options": {"file_kinds": ("question",)}},
    {"name": "só resumo", "mode": "hybrid", "rerank": True, "extra_options": {"file_kinds": ("summary",)}},
    {"name": "velho não vota", "mode": "hybrid", "rerank": True, "extra_options": {"stale_factor": 0.0}},
    {"name": "velho vota inteiro", "mode": "hybrid", "rerank": True, "extra_options": {"stale_factor": 1.0}},
    {"name": "hybrid -extras", "mode": "hybrid", "rerank": False, "extra_weights": (0.0, 0.0)},
    {"name": "hybrid", "mode": "hybrid", "rerank": False},
]


def _rank(rank: int | None) -> str:
    return f"#{rank}" if rank else "--"


def _print_diff(diffs: list[ConfigDiff], baseline: dict, meta: dict, verbose: bool) -> None:
    old = baseline.get("meta", {})
    print(f"\n\033[1mComparação com o baseline de {old.get('date', '?')}\033[0m")
    changed = [f"{k}: {old.get(k)} -> {meta[k]}" for k in meta if k not in ("date", "cases") and old.get(k) != meta[k]]
    if changed:
        print("  atenção, a rodada não é igual à do baseline: " + "; ".join(changed))
    if not diffs:
        print("  nenhuma configuração em comum com o baseline")
    for d in diffs:
        deltas = " ".join(f"{k} {was:.2f}->{now:.2f} ({now - was:+.2f})" for k, (was, now) in d.metrics.items())
        print(f"  {d.name}: {deltas}")
        extra = [f"{d.only_now} perguntas novas" if d.only_now else "",
                 f"{d.only_before} do baseline fora desta rodada" if d.only_before else ""]
        print(f"    {len(d.improved)} subiram, {len(d.worsened)} desceram"
              + "".join(f"; {e}" for e in extra if e))
        shown = [("desceu", d.worsened)] + ([("subiu", d.improved)] if verbose else [])
        for label, moved in shown:
            for m in moved:
                print(f"    {label}: {_rank(m.before):>3} -> {_rank(m.after):>3}  {m.query}")


def _print_subset(reports: list[ConfigReport], only: set[str]) -> None:
    print(f"\nSó as perguntas cuja resposta está num arquivo com resumo ({len(only)}): é onde os vetores de resumo "
          "podem agir")
    if not only:
        print("  nenhuma: resuma os arquivos dessas respostas (fila file_summaries) para medir")
        return
    print(f"{'configuração':<20} {'hit@1':>6} {'hit@3':>6} {'hit@k':>6} {'MRR':>6}")
    for r in reports:
        m = r.metrics(only=only)
        print(f"{r.name:<20} {m['hit@1']:>6.2f} {m['hit@3']:>6.2f} {m['hit@k']:>6.2f} {m['mrr']:>6.2f}")


def _print_report(reports: list[ConfigReport], n_cases: int, top_k: int, split: str | None, verbose: bool) -> None:
    print(f"{n_cases} perguntas{f' ({split})' if split else ''}, top {top_k}\n")
    print(f"{'configuração':<20} {'hit@1':>6} {'hit@3':>6} {'hit@k':>6} {'MRR':>6} {'cand':>6}   erros: recuperação / ordenação")
    for r in reports:
        m = r.metrics()
        retrieval, ranking = (len(queries) for queries in r.misses_by_stage().values())
        print(f"{r.name:<20} {m['hit@1']:>6.2f} {m['hit@3']:>6.2f} {m['hit@k']:>6.2f} {m['mrr']:>6.2f} {m['cand']:>6.2f}"
              f"   {retrieval:>18} / {ranking}")
    print("\ncand: a resposta estava entre os candidatos da fusão (teto da ordenação). Erro de recuperação: nem "
          "chegou aos candidatos;\nde ordenação: chegou e ficou fora do top (rerank ou diversificação).")
    tags = reports[0].tags() if reports else []
    if tags:
        counts = {t: sum(1 for c in reports[0].results if c.tag == t) for t in tags}
        print("\nMRR por categoria (nº de perguntas)")
        print(f"{'configuração':<20} " + " ".join(f"{t + f' ({counts[t]})':>11}" for t in tags))
        for r in reports:
            print(f"{r.name:<20} " + " ".join(f"{r.metrics(t)['mrr']:>11.2f}" for t in tags))
    if verbose:
        for r in reports:
            print(f"\n\033[1m{r.name}\033[0m")
            for c in r.results:
                where = "" if c.rank else (f"   [{c.miss_stage}"
                                           + (f", candidato #{c.candidate_rank}" if c.candidate_rank else "")
                                           + f"] (veio: {', '.join(c.top)})")
                print(f"  {_rank(c.rank):>3}  [{c.tag}] {c.query}{where}")


async def _cmd_eval(a: argparse.Namespace, settings: Settings) -> None:
    configs = [c for c in _EVAL_CONFIGS if a.no_rerank is False or not c["rerank"]]
    if a.rerank_variants:
        configs = CodeIndexService.rerank_variants()
    elif a.extras:
        configs = _EXTRAS_CONFIGS
    elif a.grid:
        configs = [{"name": f"pesos {v}/{k}", "mode": "hybrid", "rerank": False, "weights": (v, k)} for v, k in _EVAL_GRID]
    # lê o baseline antes de rodar: nome errado falha sem gastar consultas
    baseline = (CodeIndexService.load_eval_baseline(CodeIndexService.eval_baseline_path(a.file, a.compare))
                if a.compare else None)
    async with open_service(settings) as svc:
        cases = svc.load_eval_cases(a.file, a.split)
        reports = await svc.evaluate(cases, configs, top_k=a.top_k)
        meta = await svc.eval_meta(cases, top_k=a.top_k, split=a.split)
        summarized = await svc.eval_summarized(cases) if a.extras else None
    diffs = CodeIndexService.compare_eval(reports, baseline) if baseline else []
    saved = CodeIndexService.eval_baseline_path(a.file, a.save) if a.save else None
    if saved:
        CodeIndexService.save_eval_baseline(saved, reports, meta)
    if a.json:
        out: dict = {"meta": meta, "configs": [r.to_dict() for r in reports]}
        if baseline:
            out["compare"] = [{"name": d.name, "metrics": {k: {"before": was, "now": now} for k, (was, now) in d.metrics.items()},
                               "improved": [vars(m) for m in d.improved], "worsened": [vars(m) for m in d.worsened]}
                              for d in diffs]
        if summarized is not None:
            out["summarized"] = {"cases": len(summarized),
                                 "configs": {r.name: r.metrics(only=summarized) for r in reports}}
        print_json(out)
        return
    _print_report(reports, len(cases), a.top_k, a.split, a.verbose)
    if summarized is not None:
        _print_subset(reports, summarized)
    if baseline:
        _print_diff(diffs, baseline, meta, a.verbose)
    if saved:
        print(f"\nbaseline gravado em {saved}")


def register(sub: Subparsers, settings: Settings) -> None:
    ev = sub.add_parser("eval", help="mede a qualidade da busca com perguntas de resposta conhecida")
    ev.add_argument("file", nargs="?", default="evals/queries.json")
    ev.add_argument("-k", "--top-k", type=int, default=10)
    ev.add_argument("--no-rerank", action="store_true", help="pula as configurações com rerank (só embeddings)")
    ev.add_argument("--grid", action="store_true", help="compara pesos da fusão vetor/termos (sem rerank)")
    ev.add_argument("--rerank-variants", action="store_true",
                    help="compara as variações do rerank (instrução, cabeçalho, só código) com a busca sem rerank")
    ev.add_argument("--extras", action="store_true",
                    help="compara a busca com e sem os vetores extras (só perguntas, só resumo, resumo velho), "
                         "também só nas perguntas cujo arquivo tem resumo")
    ev.add_argument("--split", choices=_SPLITS,
                    help="só uma parte das perguntas: dev (~70%%, para calibrar) ou test (~30%%, para confirmar)")
    ev.add_argument("--save", metavar="NOME",
                    help="grava a rodada como baseline (evals/baselines/NOME.json, ou um caminho .json)")
    ev.add_argument("--compare", metavar="NOME",
                    help="compara com um baseline gravado: diferença das métricas e perguntas que desceram")
    ev.add_argument("-v", "--verbose", action="store_true", help="mostra cada pergunta e o que veio nos erros")
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(fn=_cmd_eval)
