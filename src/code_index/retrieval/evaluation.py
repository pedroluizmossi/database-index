"""Avaliação da busca: perguntas com resposta conhecida -> métricas.

Um caso acerta na posição N se o N-ésimo resultado é de um dos arquivos esperados (repo + fim do
caminho; com `line`, o trecho precisa conter a linha). Métricas por configuração:
  - hit@1 / hit@3 / hit@k: fração de casos com acerto até aquela posição;
  - MRR: média de 1/posição do primeiro acerto (0 quando não acerta);
  - cand: fração de casos em que a resposta estava entre os candidatos da fusão (antes do rerank e
    da diversificação). É o teto do que a ordenação consegue entregar.

Um erro tem etapa (`miss_stage`): "recuperação" quando a resposta nem chegou aos candidatos (mexer no
rerank não resolve), "ordenação" quando chegou e ficou fora dos `top_k` (rerank ou diversificação).

Separação dev/test (`split_of`): cada pergunta cai sempre no mesmo conjunto, pelo hash do texto. Calibre
pesos e opções em `dev` e confirme em `test`, que não deve orientar ajustes.

Baseline: o resultado de uma rodada gravado em JSON (`baseline_payload`), para comparar com a próxima
(`compare`): diferença das métricas e as perguntas que subiram ou desceram.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.models import Hit

SPLITS = ("dev", "test")
TEST_PERCENT = 30
STAGE_RETRIEVAL = "recuperação"
STAGE_RANKING = "ordenação"


@dataclass(frozen=True)
class Expected:
    repo: str            # nome do repo como em list_repos, só 'repo', ou '*' (qualquer)
    path: str            # fim do caminho; começando com '/', o caminho exato a partir da raiz do repo
    line: int | None = None


def split_of(query: str) -> str:
    """'test' para ~30% das perguntas, 'dev' para o resto. Depende só do texto: incluir ou remover
    casos não muda o conjunto dos outros."""
    bucket = int.from_bytes(hashlib.sha256(query.strip().lower().encode()).digest()[:4], "big") % 100
    return "test" if bucket < TEST_PERCENT else "dev"


@dataclass(frozen=True)
class EvalCase:
    query: str
    expect: tuple[Expected, ...]
    repos: tuple[str, ...] = ()
    kind: str | None = None
    tag: str = ""                    # categoria (code, doc, en, ident...) para métricas separadas
    language: str | None = None      # filtros da busca, como em `search`
    path_glob: str | None = None

    @property
    def split(self) -> str:
        return split_of(self.query)


@dataclass
class CaseResult:
    query: str
    rank: int | None                 # posição (1-based) do primeiro acerto
    top: list[str] = field(default_factory=list)
    tag: str = ""
    candidate_rank: int | None = None  # posição do primeiro acerto entre os candidatos da fusão

    @property
    def in_candidates(self) -> bool:
        return self.rank is not None or self.candidate_rank is not None

    @property
    def miss_stage(self) -> str | None:
        """Onde a resposta se perdeu (None quando o caso acertou)."""
        if self.rank is not None:
            return None
        return STAGE_RANKING if self.candidate_rank is not None else STAGE_RETRIEVAL


@dataclass
class ConfigReport:
    name: str
    results: list[CaseResult]

    def metrics(self, tag: str | None = None, only: set[str] | None = None) -> dict[str, float]:
        """Métricas de todos os casos, de uma categoria (`tag`) ou de um subconjunto de perguntas (`only`)."""
        results = [r for r in self.results if (tag is None or r.tag == tag) and (only is None or r.query in only)]
        n = len(results) or 1
        ranks = [r.rank for r in results]

        def hit_at(k: int) -> float:
            return round(sum(1 for r in ranks if r is not None and r <= k) / n, 3)

        return {
            "hit@1": hit_at(1),
            "hit@3": hit_at(3),
            "hit@k": round(sum(1 for r in ranks if r is not None) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "cand": round(sum(1 for r in results if r.in_candidates) / n, 3),
        }

    def tags(self) -> list[str]:
        return sorted({r.tag for r in self.results if r.tag})

    def misses_by_stage(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {STAGE_RETRIEVAL: [], STAGE_RANKING: []}
        for r in self.results:
            if r.miss_stage:
                out[r.miss_stage].append(r.query)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "metrics": self.metrics(),
                "by_tag": {t: self.metrics(t) for t in self.tags()},
                "misses": [r.query for r in self.results if r.rank is None],
                "misses_by_stage": self.misses_by_stage(),
                "cases": [{"query": r.query, "tag": r.tag, "rank": r.rank, "candidate_rank": r.candidate_rank}
                          for r in self.results]}


def load_cases(path: Path, split: str | None = None) -> list[EvalCase]:
    """Casos do arquivo; com `split` ('dev' ou 'test'), só os daquele conjunto."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"split inválido: {split} (use dev ou test)")
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in data["cases"]:
        expect = tuple(Expected(e["repo"], e["path"], e.get("line")) for e in c["expect"])
        if not c.get("query") or not expect:
            raise ValueError(f"caso inválido em {path}: {c}")
        cases.append(EvalCase(c["query"], expect, tuple(c.get("repos") or ()), c.get("kind"), c.get("tag", ""),
                              c.get("language"), c.get("path_glob")))
    return [c for c in cases if split is None or c.split == split]


def _repo_matches(expected: str, actual: str) -> bool:
    return expected == "*" or actual == expected or actual.endswith("__" + expected)


def matches(hit: Hit, exp: Expected) -> bool:
    if not _repo_matches(exp.repo, hit.repo):
        return False
    if exp.path.startswith("/"):                      # ancorado na raiz do repo: caminho exato
        if hit.path != exp.path[1:]:
            return False
    elif not (hit.path == exp.path or hit.path.endswith("/" + exp.path)):
        return False
    return exp.line is None or hit.start_line <= exp.line <= hit.end_line


def expects_any(cases: list[EvalCase], files: set[tuple[str, str]]) -> set[str]:
    """Perguntas cuja resposta esperada está num destes arquivos (repo, caminho): ex.: os que têm resumo, para
    medir os vetores de resumo onde eles podem agir (na métrica geral o efeito some)."""
    probes = [Hit(repo=r, path=p, language="", start_line=1, end_line=10**9, score=0.0, code="") for r, p in files]
    out = set()
    for case in cases:
        loose = tuple(Expected(e.repo, e.path, None) for e in case.expect)
        if any(matches(h, e) for h in probes for e in loose):
            out.add(case.query)
    return out


def first_hit(hits: list[Hit], expect: tuple[Expected, ...]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if any(matches(h, e) for e in expect):
            return i
    return None


# ------------------------------------------------------------------ baseline

def baseline_payload(reports: list[ConfigReport], meta: dict[str, Any]) -> dict[str, Any]:
    """Uma rodada pronta para gravar: `meta` (data, modelos, top_k, split...) + o resultado por configuração."""
    return {"meta": meta, "configs": [r.to_dict() for r in reports]}


def save_baseline(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("configs"), list):
        raise ValueError(f"baseline inválido: {path}")
    return data


@dataclass
class Moved:
    query: str
    before: int | None
    after: int | None


@dataclass
class ConfigDiff:
    name: str
    metrics: dict[str, tuple[float, float]]   # métrica -> (antes, agora), só nas perguntas em comum
    improved: list[Moved]
    worsened: list[Moved]
    only_now: int                             # perguntas que o baseline não tinha
    only_before: int                          # perguntas do baseline que não rodaram agora


def compare(reports: list[ConfigReport], baseline: dict[str, Any]) -> list[ConfigDiff]:
    """Compara cada configuração com a de mesmo nome no baseline, nas perguntas presentes nos dois.
    Configuração que o baseline não tem fica de fora."""
    before_by_name = {c["name"]: c for c in baseline["configs"]}
    out = []
    for rep in reports:
        old = before_by_name.get(rep.name)
        if old is None:
            continue
        old_cases = {c["query"]: c for c in old.get("cases", [])}
        common = [r for r in rep.results if r.query in old_cases]
        old_report = ConfigReport(rep.name, [
            CaseResult(r.query, old_cases[r.query]["rank"], tag=r.tag,
                       candidate_rank=old_cases[r.query].get("candidate_rank")) for r in common])
        now, was = ConfigReport(rep.name, common).metrics(), old_report.metrics()
        improved, worsened = [], []
        for r, o in zip(common, old_report.results):
            a, b = r.rank or 10**9, o.rank or 10**9
            if a < b:
                improved.append(Moved(r.query, o.rank, r.rank))
            elif a > b:
                worsened.append(Moved(r.query, o.rank, r.rank))
        out.append(ConfigDiff(rep.name, {k: (was[k], now[k]) for k in now}, improved, worsened,
                              only_now=len(rep.results) - len(common), only_before=len(old_cases) - len(common)))
    return out
