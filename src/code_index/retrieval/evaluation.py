"""Avaliação da busca: perguntas com resposta conhecida -> métricas (pura, sem I/O).

Um caso acerta na posição N se o N-ésimo resultado é de um dos arquivos esperados (repo + fim do
caminho; com `line`, o trecho precisa conter a linha). Métricas por configuração:
  - hit@1 / hit@3 / hit@k: fração de casos com acerto até aquela posição;
  - MRR: média de 1/posição do primeiro acerto (0 quando não acerta).
"""

from __future__ import annotations

import json
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
                "misses": [r.query for r in self.results if r.rank is None]}


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
