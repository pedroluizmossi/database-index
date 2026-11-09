"""Registro de preferências: validação, resolução em camadas e tolerância à evolução do registro."""

from __future__ import annotations

import pytest

from code_index.domain.preferences import PREFS, boolean, choice, integer, resolve, schema, validate_changes

REG = {p.key: p for p in (
    choice("ui.theme", "Tema", ("system", "light", "dark"), "system"),
    integer("ui.page", "Página", 10, minimum=5, maximum=50),
    boolean("search.rerank", "Rerank", True, applies_to="search"),
)}


def test_padroes_quando_nada_foi_gravado() -> None:
    assert resolve({}, prefs=REG) == {"ui.theme": "system", "ui.page": 10, "search.rerank": True}


def test_valor_da_pessoa_e_override_da_chamada() -> None:
    stored = {"ui.theme": "dark", "ui.page": 20}
    assert resolve(stored, prefs=REG)["ui.theme"] == "dark"
    assert resolve(stored, {"ui.page": 30, "ui.theme": None}, prefs=REG) == {
        "ui.theme": "dark", "ui.page": 30, "search.rerank": True}
    with pytest.raises(ValueError):
        resolve(stored, {"ui.page": 999}, prefs=REG)


def test_valor_guardado_invalido_ou_orfao_nao_quebra() -> None:
    # a config mudou de tipo/faixa ou saiu do registro depois de gravada
    assert resolve({"ui.theme": "roxo", "ui.page": "20", "antiga.removida": 1}, prefs=REG) == {
        "ui.theme": "system", "ui.page": 10, "search.rerank": True}


def test_validate_changes() -> None:
    assert validate_changes({"ui.theme": "light", "ui.page": None}, REG) == ({"ui.theme": "light"}, ["ui.page"])
    for bad in ({"nao.existe": 1}, {"ui.theme": "roxo"}, {"ui.page": 4}, {"ui.page": True}, {"search.rerank": "sim"}):
        with pytest.raises(ValueError):
            validate_changes(bad, REG)


def test_schema_do_registro_real() -> None:
    items = schema()
    assert [i["key"] for i in items] == list(PREFS)
    for item in items:
        assert PREFS[item["key"]].parse(item["default"]) == item["default"]  # todo padrão passa na própria validação
        assert item["type"] in ("choice", "int", "bool", "text")
