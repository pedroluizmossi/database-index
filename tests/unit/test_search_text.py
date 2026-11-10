"""domain/search_text: tokenização para busca em código."""

from __future__ import annotations

import pytest

from code_index.domain.search_text import code_terms, fusion_weights, query_kind, query_terms, split_identifier


@pytest.mark.parametrize(
    ("ident", "parts"),
    [
        ("HmacServiceImpl", ["hmac", "service", "impl"]),
        ("get_user_name", ["get", "user", "name"]),
        ("XMLHttpRequest", ["xml", "http", "request"]),
        ("v2Api", ["2", "api"]),  # partes de 1 letra são descartadas
        ("MAX_RETRY", ["max", "retry"]),
    ],
)
def test_split_identifier(ident: str, parts: list[str]) -> None:
    assert split_identifier(ident) == parts


def test_code_terms_mantem_inteiro_e_partes_e_frequencia() -> None:
    terms = code_terms("HmacServiceImpl sign(payload); sign()").split()
    assert terms[:4] == ["hmacserviceimpl", "hmac", "service", "impl"]
    assert terms.count("sign") == 2  # frequência preservada para o BM25


def test_code_terms_palavras_comuns_e_acentos() -> None:
    assert code_terms("// valida a assinatura não 42") == "valida assinatura nao"


def test_query_terms_remove_stopwords_e_repeticao() -> None:
    assert query_terms("como o webhook é reenviado quando o webhook falha") == [
        "webhook", "reenviado", "quando", "falha",
    ]
    assert query_terms("HmacServiceImpl") == ["hmacserviceimpl", "hmac", "service", "impl"]


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("HmacServiceImpl", "identifier"),
        ("get_user_name", "identifier"),
        ("br.com.x.Classe", "identifier"),
        ("Classe::metodo", "identifier"),
        ("processa()", "identifier"),
        ("MAX", "identifier"),
        ("HTTPServer", "identifier"),
        ("XMLParser", "identifier"),
        ("JWTFilter", "identifier"),
        ("IOUtils", "identifier"),
        ("webhook", "natural"),
        ("Login", "natural"),
        ("como o webhook é reenviado", "natural"),
        ("Como funciona o login", "natural"),
        ("", "natural"),
    ],
)
def test_query_kind(query: str, kind: str) -> None:
    assert query_kind(query) == kind


def test_pesos_da_fusao() -> None:
    vec, kw = fusion_weights("natural")
    assert vec > kw
    vec, kw = fusion_weights("identifier")
    assert kw > vec


def test_consulta_procura_sem_acento_com_acento_e_singular() -> None:
    assert query_terms("validação das requisições") == [
        "validacao", "validação", "requisicoes", "requisições", "requisicao", "requisição",
    ]
    assert query_terms("remove tokens expirados") == ["remove", "tokens", "token", "expirados", "expirado"]


def test_identificador_nao_ganha_singular() -> None:
    assert query_terms("UserKeys") == ["userkeys", "user", "keys"]


def test_stopwords_acentuadas_ou_nao() -> None:
    assert query_terms("o token é válido e está ativo") == ["token", "valido", "válido", "ativo"]


@pytest.mark.parametrize(
    ("word", "sing"),
    [("tokens", "token"), ("requisicoes", "requisicao"), ("entities", "entity"), ("status", None),
     ("class", None), ("redis", None), ("dias", None), ("permissões", "permissão")],
)
def test_singular(word: str, sing: str | None) -> None:
    from code_index.domain.search_text import singular

    assert singular(word) == sing
