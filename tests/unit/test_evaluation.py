"""retrieval.evaluation: acerto por repo/caminho/linha e métricas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_index.domain.models import Hit
from code_index.retrieval.evaluation import (
    CaseResult, ConfigReport, Expected, baseline_payload, compare, first_hit, load_baseline, load_cases, save_baseline,
    split_of,
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
    docs = [hit("org__svc", "docs/README.md"), hit("org__svc", "README.md")]
    assert first_hit(docs, (Expected("svc", "README.md"),)) == 1          # fim do caminho: qualquer README
    assert first_hit(docs, (Expected("svc", "/README.md"),)) == 2         # '/' ancora na raiz do repo


def test_metricas() -> None:
    rep = ConfigReport("x", [CaseResult("a", 1), CaseResult("b", 2), CaseResult("c", None), CaseResult("d", 4)])
    assert rep.metrics() == {"hit@1": 0.25, "hit@3": 0.5, "hit@k": 0.75, "mrr": round((1 + 0.5 + 0.25) / 4, 3),
                             "cand": 0.75}
    assert rep.to_dict()["misses"] == ["c"]


def test_carrega_casos_e_recusa_caso_sem_resposta(tmp_path: Path) -> None:
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"cases": [{"query": "q", "repos": ["r"], "expect": [{"repo": "r", "path": "a.py"}]}]}))
    (case,) = load_cases(f)
    assert case.repos == ("r",) and case.expect == (Expected("r", "a.py"),)
    f.write_text(json.dumps({"cases": [{"query": "q", "kind": "code", "language": "go", "path_glob": "**/*.go",
                                        "expect": [{"repo": "*", "path": "a.go", "line": 3}]}]}))
    (case,) = load_cases(f)
    assert (case.kind, case.language, case.path_glob, case.repos) == ("code", "go", "**/*.go", ())
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
    assert rep.metrics("doc") == {"hit@1": 0.0, "hit@3": 0.5, "hit@k": 0.5, "mrr": 0.25, "cand": 0.5}
    assert rep.to_dict()["by_tag"]["doc"]["mrr"] == 0.25


def test_conjunto_tem_todas_as_categorias() -> None:
    cases = load_cases(Path(__file__).parents[2] / "evals" / "queries.json")
    assert len(cases) >= 150
    assert {c.tag for c in cases} == {"code", "doc", "en", "ident"}


def test_etapa_do_erro_e_teto_dos_candidatos() -> None:
    rep = ConfigReport("x", [
        CaseResult("acertou", 2, candidate_rank=7),
        CaseResult("perdeu na ordenação", None, candidate_rank=31),
        CaseResult("nem foi candidato", None),
    ])
    assert [r.miss_stage for r in rep.results] == [None, "ordenação", "recuperação"]
    assert rep.metrics()["cand"] == round(2 / 3, 3)
    assert rep.misses_by_stage() == {"recuperação": ["nem foi candidato"], "ordenação": ["perdeu na ordenação"]}
    assert rep.to_dict()["cases"][1] == {"query": "perdeu na ordenação", "tag": "", "rank": None, "candidate_rank": 31}


def test_split_e_estavel_e_separa_o_conjunto(tmp_path: Path) -> None:
    assert split_of("Onde valida o token ") == split_of("onde valida o token")   # só o texto decide
    path = Path(__file__).parents[2] / "evals" / "queries.json"
    everything, dev, test = load_cases(path), load_cases(path, "dev"), load_cases(path, "test")
    assert len(dev) + len(test) == len(everything)
    assert not {c.query for c in dev} & {c.query for c in test}
    assert 0.2 < len(test) / len(everything) < 0.4
    assert {c.tag for c in test} == {"code", "doc", "en", "ident"}             # toda categoria tem caso reservado
    with pytest.raises(ValueError):
        load_cases(path, "treino")


def test_baseline_grava_le_e_compara(tmp_path: Path) -> None:
    before = ConfigReport("hybrid", [CaseResult("a", 1), CaseResult("b", 5), CaseResult("c", None), CaseResult("saiu", 1)])
    path = tmp_path / "baselines" / "antes.json"
    save_baseline(path, baseline_payload([before], {"date": "2026-01-01", "top_k": 10}))
    baseline = load_baseline(path)
    assert baseline["meta"]["top_k"] == 10

    now = ConfigReport("hybrid", [CaseResult("a", 3), CaseResult("b", 1), CaseResult("c", 2), CaseResult("nova", None)])
    other = ConfigReport("config que o baseline não tem", [CaseResult("a", 1)])
    (diff,) = compare([now, other], baseline)
    assert [(m.query, m.before, m.after) for m in diff.improved] == [("b", 5, 1), ("c", None, 2)]
    assert [(m.query, m.before, m.after) for m in diff.worsened] == [("a", 1, 3)]
    assert (diff.only_now, diff.only_before) == (1, 1)
    assert diff.metrics["hit@1"] == (round(1 / 3, 3), round(1 / 3, 3))           # só as 3 perguntas em comum
    assert diff.metrics["hit@k"] == (round(2 / 3, 3), 1.0)

    path.write_text("[]")
    with pytest.raises(ValueError):
        load_baseline(path)
