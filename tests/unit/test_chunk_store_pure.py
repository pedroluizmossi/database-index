"""Funções puras do ChunkStore: montagem do tsquery de palavras-chave e conversão de glob para LIKE."""

from __future__ import annotations

import pytest

from code_index.infra.chunk_store import _STOPWORDS, glob_to_like, keyword_tsquery


@pytest.mark.parametrize("query", ["", "   ", "de a o", "the and of", "ab cd é"])
def test_sem_termos_significativos_nao_ha_tsquery(query: str) -> None:
    assert keyword_tsquery(query) is None


def test_termos_significativos_unidos_por_or_na_ordem_original() -> None:
    assert keyword_tsquery("como funciona o retry de conexão") == "funciona | retry | conexão"


def test_termos_repetidos_aparecem_uma_vez_sem_distincao_de_caixa() -> None:
    assert keyword_tsquery("Retry retry RETRY") == "retry"


def test_termos_com_menos_de_tres_caracteres_sao_descartados() -> None:
    assert keyword_tsquery("a1 ab xyz") == "xyz"
    assert keyword_tsquery("123") == "123"


def test_stopwords_em_ingles_e_portugues_sao_descartadas() -> None:
    assert keyword_tsquery("how does the parser work") == "parser | work"
    assert "the" in _STOPWORDS and "de" in _STOPWORDS


def test_pontuacao_e_operadores_de_tsquery_nao_passam() -> None:
    # Só sobram tokens \w: nada de &, |, !, :* ou aspas chega ao to_tsquery.
    assert keyword_tsquery("foo & bar! :* 'x' | (baz)") == "foo | bar | baz"


def test_identificadores_com_underscore_sao_um_termo() -> None:
    assert keyword_tsquery("getUserById(user_id)?") == "getuserbyid | user_id"


@pytest.mark.parametrize(
    ("glob", "like"),
    [
        ("*.py", "%.py"),
        ("src/**/*.py", "src/%/%.py"),
        ("**/*.ts", "%/%.ts"),
        ("**", "%"),
        ("file?.txt", "file_.txt"),
        ("src/app", "src/app"),
    ],
)
def test_glob_para_like(glob: str, like: str) -> None:
    assert glob_to_like(glob) == like


def test_underscore_e_percent_literais_sao_escapados() -> None:
    # Sem escape, "_" e "%" seriam curingas do LIKE e casariam arquivos errados.
    assert glob_to_like("test_*.py") == r"test\_%.py"
    assert glob_to_like("100%*") == r"100\%%"
