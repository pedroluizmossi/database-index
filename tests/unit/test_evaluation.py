"""retrieval.evaluation: acerto por repo/caminho/linha e métricas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_index.domain.models import Hit
from code_index.retrieval.evaluation import CaseResult, ConfigReport, Expected, first_hit, load_cases


def hit(repo: str, path: str, start: int = 1, end: int = 10) -> Hit:
    return Hit(repo=repo, path=path, language="java", start_line=start, end_line=end, score=1.0, code="")


def test_acerto_por_nome_curto_sufixo_e_linha() -> None:
    hits = [hit("org__outro", "a/X.java"), hit("org__svc", "src/service/Y.java", 100, 120)]
    assert first_hit(hits, (Expected("svc", "service/Y.java"),)) == 2
    assert first_hit(hits, (Expected("svc", "service/Y.java", line=110),)) == 2
    assert first_hit(hits, (Expected("svc", "service/Y.java", line=200),)) is None
    assert first_hit(hits, (Expected("*", "X.java"),)) == 1
    assert first_hit(hits, (Expected("svc", "Y.java"),)) == 2          # fim do caminho
    assert first_hit(hits, (Expected("svc", "ice/Y.java"),)) is None   # não corta nome de pasta


def test_metricas() -> None:
    rep = ConfigReport("x", [CaseResult("a", 1), CaseResult("b", 2), CaseResult("c", None), CaseResult("d", 4)])
    assert rep.metrics() == {"hit@1": 0.25, "hit@3": 0.5, "hit@k": 0.75, "mrr": round((1 + 0.5 + 0.25) / 4, 3)}
    assert rep.to_dict()["misses"] == ["c"]


def test_carrega_casos_e_recusa_caso_sem_resposta(tmp_path: Path) -> None:
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"cases": [{"query": "q", "repos": ["r"], "expect": [{"repo": "r", "path": "a.py"}]}]}))
    (case,) = load_cases(f)
    assert case.repos == ("r",) and case.expect == (Expected("r", "a.py"),)
    f.write_text(json.dumps({"cases": [{"query": "q", "expect": []}]}))
    with pytest.raises(ValueError):
        load_cases(f)


def test_conjunto_do_projeto_carrega() -> None:
    cases = load_cases(Path(__file__).parents[2] / "evals" / "queries.json")
    assert len(cases) >= 20


def test_metricas_por_categoria() -> None:
    rep = ConfigReport("x", [CaseResult("a", 1, tag="code"), CaseResult("b", None, tag="doc"),
                             CaseResult("c", 2, tag="doc")])
    assert rep.tags() == ["code", "doc"]
    assert rep.metrics("code")["mrr"] == 1.0
    assert rep.metrics("doc") == {"hit@1": 0.0, "hit@3": 0.5, "hit@k": 0.5, "mrr": 0.25}
    assert rep.to_dict()["by_tag"]["doc"]["mrr"] == 0.25


def test_conjunto_tem_todas_as_categorias() -> None:
    cases = load_cases(Path(__file__).parents[2] / "evals" / "queries.json")
    assert len(cases) >= 60
    assert {c.tag for c in cases} == {"code", "doc", "en", "ident"}
