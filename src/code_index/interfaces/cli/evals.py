"""CLI: `code-index eval` (qualidade da busca com perguntas de resposta conhecida)."""

from __future__ import annotations

import argparse

from ...services import CodeIndexService, open_service
from ...settings import Settings
from .common import Subparsers, print_json

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
        print_json([r.to_dict() for r in reports])
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


def register(sub: Subparsers, settings: Settings) -> None:
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
