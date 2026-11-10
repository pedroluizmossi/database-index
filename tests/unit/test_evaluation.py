"""retrieval.evaluation: acerto por repo/caminho/linha e métricas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_index.domain.models import Hit
from code_index.retrieval.evaluation import (
    CaseResult, ConfigReport, Expected, compare, compare_all, first_hit, load_cases, load_reports,
)


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


def _report(name: str, ranks: list[int | None]) -> ConfigReport:
    return ConfigReport(name, [CaseResult(f"q{i}", r) for i, r in enumerate(ranks)])


def test_compare_melhora_consistente_e_significativa() -> None:
    c = compare(_report("antes", [3] * 20), _report("depois", [1] * 20))
    assert (c.wins, c.losses, c.ties) == (20, 0, 0)
    assert c.delta_mrr == round(1 - 1 / 3, 3) and c.ci_low > 0
    assert c.significant and c.verdict == "melhor"
    assert compare(_report("depois", [1] * 20), _report("antes", [3] * 20)).verdict == "pior"


def test_compare_diferenca_pequena_e_ruido() -> None:
    # uma pergunta melhorou, outra piorou um pouco menos: MRR sobe, mas não dá para afirmar nada
    base = _report("a", [1, 2, None, 4] * 5)
    other = _report("b", [1, 2, None, 4] * 4 + [1, 1, None, 5])
    c = compare(base, other)
    assert c.delta_mrr > 0 and c.wins == 1 and c.losses == 1
    assert not c.significant and c.verdict == "indistinguível"
    assert c.ci_low <= 0 <= c.ci_high


def test_compare_p_exato_identicos_e_deterministico() -> None:
    same = compare(_report("a", [1, 2, None]), _report("b", [1, 2, None]))
    assert same.p_value == 1.0 and same.delta_mrr == 0 and same.ties == 3
    # 5 vitórias em 5: só 2 das 32 trocas de sinal são tão extremas
    assert compare(_report("a", [2] * 5), _report("b", [1] * 5)).p_value == round(2 / 32, 4)
    big_a, big_b = _report("a", [2, 1, 3] * 10), _report("b", [1, 2, 1] * 10)   # > 16 diferenças: amostragem
    assert compare(big_a, big_b) == compare(big_a, big_b)


def test_compare_so_perguntas_em_comum_e_baseline(tmp_path: Path) -> None:
    before = [_report("hybrid", [2, 2, 2]), _report("keyword", [1, 1, 1])]
    f = tmp_path / "antes.json"
    f.write_text(json.dumps([r.to_dict() for r in before]))
    loaded = load_reports(f)
    assert [r.rank for r in loaded[0].results] == [2, 2, 2]
    after = [ConfigReport("hybrid", [CaseResult("q0", 1), CaseResult("nova", 1)]), _report("semantic", [1])]
    (c,) = compare_all(after, loaded)            # 'semantic' não existia antes; 'nova' não entra
    assert (c.base, c.other, c.n, c.wins) == ("hybrid", "hybrid", 1, 1)
    assert [c.other for c in compare_all(before)] == ["keyword"]   # sem baseline: contra a primeira
    with pytest.raises(ValueError):
        compare(_report("a", [1]), ConfigReport("b", [CaseResult("outra", 1)]))
    with pytest.raises(ValueError):
        ConfigReport.from_dict({"name": "velho", "metrics": {}})
