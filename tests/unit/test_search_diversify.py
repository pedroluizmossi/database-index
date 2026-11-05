"""retrieval.search.diversify: trechos vizinhos fundidos e no máximo N por arquivo."""

from __future__ import annotations

from code_index.domain.models import Hit
from code_index.retrieval.search import diversify


def hit(path: str, start: int, end: int, repo: str = "r") -> Hit:
    code = "\n".join(f"{path}:{i}" for i in range(start, end + 1))
    return Hit(repo=repo, path=path, language="java", start_line=start, end_line=end, score=1.0, code=code)


def test_funde_trechos_sobrepostos_do_mesmo_arquivo() -> None:
    out = diversify([hit("A", 1, 5), hit("A", 4, 9), hit("B", 1, 2)], top_k=5)
    assert [(h.path, h.start_line, h.end_line) for h in out] == [("A", 1, 9), ("B", 1, 2)]
    assert out[0].code.splitlines() == [f"A:{i}" for i in range(1, 10)]


def test_funde_trecho_anterior_e_encostado() -> None:
    out = diversify([hit("A", 10, 20), hit("A", 5, 10), hit("A", 21, 22)], top_k=5)
    assert len(out) == 1 and (out[0].start_line, out[0].end_line) == (5, 22)


def test_limita_por_arquivo_e_completa_com_os_proximos() -> None:
    hits = [hit("A", 1, 2), hit("A", 10, 12), hit("A", 20, 22), hit("B", 1, 2), hit("C", 1, 2)]
    out = diversify(hits, top_k=4)
    assert [(h.path, h.start_line) for h in out] == [("A", 1), ("A", 10), ("B", 1), ("C", 1)]


def test_mesmo_caminho_em_repos_diferentes_nao_se_mistura() -> None:
    out = diversify([hit("A", 1, 5, repo="x"), hit("A", 3, 8, repo="y")], top_k=5)
    assert [(h.repo, h.end_line) for h in out] == [("x", 5), ("y", 8)]


def test_trecho_gigante_repetido_some_sem_crescer() -> None:
    big = "x" * 5000
    a = Hit(repo="r", path="m.json", language="json", start_line=1, end_line=1, score=1.0, code=big)
    b = Hit(repo="r", path="m.json", language="json", start_line=1, end_line=1, score=0.9, code=big)
    out = diversify([a, b], top_k=5)
    assert len(out) == 1 and out[0].code == big


async def test_consulta_vazia_e_erro_claro_sem_chamar_a_voyage() -> None:
    import pytest

    from code_index.retrieval.search import Searcher
    from code_index.settings import Settings

    searcher = Searcher(Settings(), chunks=None)  # type: ignore[arg-type]  # não pode chegar no banco
    for q in ("", "   "):
        with pytest.raises(ValueError, match="consulta vazia"):
            await searcher.search(q)


def doc(path: str) -> Hit:
    return Hit(repo="docs__wiki", path=path, language="markdown", start_line=1, end_line=2, score=1.0,
               code=f"# {path}", kind="doc")


def test_limita_paginas_de_documentacao() -> None:
    hits = [doc("a.md"), doc("b.md"), doc("c.md"), hit("A", 1, 2), hit("B", 1, 2)]
    out = diversify(hits, top_k=4, max_docs=2)
    assert [h.path for h in out] == ["a.md", "b.md", "A", "B"]
    assert len(diversify(hits, top_k=5)) == 5  # sem limite


def test_trecho_identico_em_outro_repo_vira_also_in() -> None:
    a, b, c = hit("pom.xml", 1, 9, repo="x"), hit("pom.xml", 1, 9, repo="y"), hit("pom.xml", 1, 9, repo="z")
    out = diversify([a, b, c, hit("Main.java", 1, 3, repo="y")], top_k=3)
    assert [(h.repo, h.path) for h in out] == [("x", "pom.xml"), ("y", "Main.java")]
    assert out[0].also_in == ["y", "z"]


def test_padrao_de_docs_por_resposta() -> None:
    from code_index.retrieval.search import default_max_docs

    assert (default_max_docs(5), default_max_docs(8), default_max_docs(20)) == (2, 2, 5)


def test_rerank_so_codigo_mantem_docs_nas_posicoes_hibridas() -> None:
    from code_index.retrieval.search import merge_code_ranking

    a, b, c = hit("A", 1, 2), hit("B", 1, 2), hit("C", 1, 2)
    d1, d2 = doc("x.md"), doc("y.md")
    hybrid = [d1, a, b, d2, c]
    assert [h.path for h in merge_code_ranking(hybrid, [c, a, b])] == ["x.md", "C", "A", "y.md", "B"]
    # reranker devolveu menos: o código que faltou mantém a ordem híbrida
    assert [h.path for h in merge_code_ranking(hybrid, [c])] == ["x.md", "C", "A", "y.md", "B"]
