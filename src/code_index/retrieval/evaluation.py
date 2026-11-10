"""Avaliação da busca: perguntas com resposta conhecida -> métricas (pura, sem I/O).

Um caso acerta na posição N se o N-ésimo resultado é de um dos arquivos esperados (repo + fim do
caminho; com `line`, o trecho precisa conter a linha). Métricas por configuração:
  - hit@1 / hit@3 / hit@k: fração de casos com acerto até aquela posição;
  - MRR: média de 1/posição do primeiro acerto (0 quando não acerta).

Comparar duas configurações (`compare`): com poucas dezenas de perguntas, uma diferença de MRR pode ser
sorte. A comparação é pareada (a mesma pergunta nas duas) e responde duas coisas sem supor distribuição:
  - teste de permutação (troca de sinal das diferenças): chance de ver uma diferença deste tamanho se
    as duas configurações fossem equivalentes (p);
  - bootstrap pareado: intervalo de 95% da diferença de MRR.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.models import Hit


@dataclass(frozen=True)
class Expected:
    repo: str            # nome do repo como em list_repos, só 'repo', ou '*' (qualquer)
    path: str            # fim do caminho
    line: int | None = None


@dataclass(frozen=True)
class EvalCase:
    query: str
    expect: tuple[Expected, ...]
    repos: tuple[str, ...] = ()
    kind: str | None = None
    tag: str = ""                    # categoria (code, doc, en, ident...) para métricas separadas


@dataclass
class CaseResult:
    query: str
    rank: int | None                 # posição (1-based) do primeiro acerto
    top: list[str] = field(default_factory=list)
    tag: str = ""


@dataclass
class ConfigReport:
    name: str
    results: list[CaseResult]

    def metrics(self, tag: str | None = None) -> dict[str, float]:
        results = [r for r in self.results if tag is None or r.tag == tag]
        n = len(results) or 1
        ranks = [r.rank for r in results]

        def hit_at(k: int) -> float:
            return round(sum(1 for r in ranks if r is not None and r <= k) / n, 3)

        return {
            "hit@1": hit_at(1),
            "hit@3": hit_at(3),
            "hit@k": round(sum(1 for r in ranks if r is not None) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        }

    def tags(self) -> list[str]:
        return sorted({r.tag for r in self.results if r.tag})

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "metrics": self.metrics(),
                "by_tag": {t: self.metrics(t) for t in self.tags()},
                "misses": [r.query for r in self.results if r.rank is None],
                "ranks": [{"query": r.query, "rank": r.rank, "tag": r.tag} for r in self.results]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigReport:
        """Relatório salvo por `to_dict` (ex.: `code-index eval --json > antes.json`)."""
        if "ranks" not in data:
            raise ValueError("relatório sem 'ranks': gere de novo com `code-index eval --json`")
        return cls(data["name"], [CaseResult(r["query"], r["rank"], tag=r.get("tag", "")) for r in data["ranks"]])


_EXACT_MAX = 16        # até 2^16 trocas de sinal: p exato; acima, amostragem
SIGNIFICANCE = 0.05


@dataclass(frozen=True)
class Comparison:
    """`other` contra `base`, nas perguntas que os dois relatórios têm em comum."""

    base: str
    other: str
    n: int
    wins: int            # perguntas em que `other` pôs a resposta mais acima
    losses: int
    delta_mrr: float     # MRR(other) - MRR(base)
    ci_low: float        # intervalo de 95% de delta_mrr (bootstrap pareado)
    ci_high: float
    p_value: float       # teste de permutação bilateral

    @property
    def ties(self) -> int:
        return self.n - self.wins - self.losses

    @property
    def significant(self) -> bool:
        return self.p_value < SIGNIFICANCE

    @property
    def verdict(self) -> str:
        if not self.significant:
            return "indistinguível"
        return "melhor" if self.delta_mrr > 0 else "pior"

    def to_dict(self) -> dict[str, Any]:
        return {"base": self.base, "other": self.other, "n": self.n, "wins": self.wins, "losses": self.losses,
                "ties": self.ties, "delta_mrr": self.delta_mrr, "ci95": [self.ci_low, self.ci_high],
                "p_value": self.p_value, "verdict": self.verdict}


def _reciprocal(rank: int | None) -> float:
    return 1 / rank if rank else 0.0


def _permutation_p(diffs: list[float], samples: int, rng: random.Random) -> float:
    """P(|soma| >= observada) trocando o sinal de cada diferença ao acaso (hipótese: tanto faz a ordem
    das duas configurações). Empates (0) não mudam nada e ficam de fora."""
    nonzero = [d for d in diffs if d]
    if not nonzero:
        return 1.0
    observed = abs(sum(nonzero)) - 1e-12
    if len(nonzero) <= _EXACT_MAX:
        sums = [0.0]
        for d in nonzero:
            sums = [s + d for s in sums] + [s - d for s in sums]
        return sum(1 for s in sums if abs(s) >= observed) / len(sums)
    extreme = sum(1 for _ in range(samples)
                  if abs(sum(d if rng.random() < 0.5 else -d for d in nonzero)) >= observed)
    return (extreme + 1) / (samples + 1)


def compare(base: ConfigReport, other: ConfigReport, *, samples: int = 10_000, seed: int = 0) -> Comparison:
    """Compara `other` com `base` pergunta a pergunta. Determinístico para o mesmo `seed`."""
    base_ranks = {r.query: r.rank for r in base.results}
    pairs = [(base_ranks[r.query], r.rank) for r in other.results if r.query in base_ranks]
    if not pairs:
        raise ValueError(f"'{base.name}' e '{other.name}' não têm perguntas em comum")
    diffs = [_reciprocal(o) - _reciprocal(b) for b, o in pairs]
    n = len(diffs)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(samples))
    return Comparison(
        base=base.name, other=other.name, n=n,
        wins=sum(1 for d in diffs if d > 0), losses=sum(1 for d in diffs if d < 0),
        delta_mrr=round(sum(diffs) / n, 3),
        ci_low=round(means[int(0.025 * (samples - 1))], 3), ci_high=round(means[int(0.975 * (samples - 1))], 3),
        p_value=round(_permutation_p(diffs, samples, rng), 4),
    )


def compare_all(reports: list[ConfigReport], baseline: list[ConfigReport] | None = None) -> list[Comparison]:
    """Sem `baseline`: cada configuração contra a primeira. Com `baseline` (execução anterior salva): cada
    configuração contra a de mesmo nome (o antes e depois de uma mudança na busca)."""
    if baseline is None:
        return [compare(reports[0], r) for r in reports[1:]]
    before = {r.name: r for r in baseline}
    return [compare(before[r.name], r) for r in reports if r.name in before]


def load_reports(path: Path) -> list[ConfigReport]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: esperado o JSON de `code-index eval --json`")
    return [ConfigReport.from_dict(d) for d in data]


def load_cases(path: Path) -> list[EvalCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in data["cases"]:
        expect = tuple(Expected(e["repo"], e["path"], e.get("line")) for e in c["expect"])
        if not c.get("query") or not expect:
            raise ValueError(f"caso inválido em {path}: {c}")
        cases.append(EvalCase(c["query"], expect, tuple(c.get("repos") or ()), c.get("kind"), c.get("tag", "")))
    return cases


def _repo_matches(expected: str, actual: str) -> bool:
    return expected == "*" or actual == expected or actual.endswith("__" + expected)


def matches(hit: Hit, exp: Expected) -> bool:
    if not _repo_matches(exp.repo, hit.repo):
        return False
    if not (hit.path == exp.path or hit.path.endswith("/" + exp.path)):
        return False
    return exp.line is None or hit.start_line <= exp.line <= hit.end_line


def first_hit(hits: list[Hit], expect: tuple[Expected, ...]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if any(matches(h, e) for e in expect):
            return i
    return None
