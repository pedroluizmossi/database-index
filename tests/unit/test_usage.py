"""Regras puras de uso (domain/usage.py) e o medidor de consumo de LLM (infra/llm.metered)."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from code_index.domain import usage as U
from code_index.infra import llm


def test_canal_por_transporte_e_tipo_de_credencial() -> None:
    assert U.channel_for("mcp", "session") == "mcp"
    assert U.channel_for("http", "session") == "ui"
    assert U.channel_for("http", "agent") == "api"
    assert U.channel_for("http", "oauth") == "api"


def test_status_dos_erros_de_caso_de_uso() -> None:
    assert U.status_for(PermissionError()) == 403
    assert U.status_for(ValueError()) == 400
    assert U.status_for(RuntimeError()) == 409
    assert U.status_for(KeyError()) == 500


def test_parametros_limpos_sem_segredos_e_cortados() -> None:
    p = U.clean_params({
        "query": "x" * 2000, "repos": [f"r{i}" for i in range(50)], "language": None, "path_glob": "",
        "auth_token": "cix_abc", "client_secret": "s", "top_k": 8, "regex": False, "obj": {"a": 1},
    })
    assert p is not None
    assert len(p["query"]) == U.MAX_PARAM_CHARS
    assert len(p["repos"]) == U.MAX_PARAM_ITEMS
    assert "auth_token" not in p and "client_secret" not in p
    assert "language" not in p and "path_glob" not in p
    assert p["top_k"] == 8 and p["regex"] is False
    assert p["obj"] == "{'a': 1}"
    assert U.clean_params({}) is None
    assert U.clean_params({"query": None}) is None


def test_contagem_e_tamanho_da_resposta() -> None:
    assert U.result_count([1, 2, 3]) == 3
    assert U.result_count({"matches": [1], "truncated": False}) == 1
    assert U.result_count({"entity": "repo:x", "relations": [1, 2]}) == 2
    assert U.result_count({"total": 300, "repos": [1, 2], "next_offset": 2}) == 2  # list_repos paginado
    assert U.result_count("texto do arquivo") is None
    assert U.result_count(None) is None
    assert U.response_bytes(None) == 0
    assert U.response_bytes("ação") == 6  # bytes UTF-8, não caracteres
    assert U.response_bytes({"a": "b"}) == len('{"a": "b"}')
    assert U.approx_tokens(400) == 100
    assert U.approx_tokens(None) == 0


@pytest.mark.parametrize(("ua", "expected"), [
    ("claude-code/2.0.31 (cli)", ("claude-code", "2.0.31")),
    ("python-httpx/0.28.1", ("python-httpx", "0.28.1")),
    ("Cursor", ("cursor", None)),
    ("Mozilla/5.0 (X11; Linux x86_64) Chrome/140", ("navegador", None)),
    ("", (None, None)),
    (None, (None, None)),
])
def test_cliente_pelo_user_agent(ua: str | None, expected: tuple[str | None, str | None]) -> None:
    assert U.client_from_user_agent(ua) == expected


def test_nome_do_cliente_mcp() -> None:
    assert U.normalize_client("  Claude Code ") == "claude code"
    assert U.normalize_client("") is None
    assert U.normalize_client(None) is None


def test_periodo_valido() -> None:
    assert U.validate_days(30) == 30
    for bad in (0, -1, U.MAX_REPORT_DAYS + 1):
        with pytest.raises(ValueError):
            U.validate_days(bad)


def test_serie_diaria_preenche_dias_sem_uso() -> None:
    rows = [{"day": "2026-11-09", "mcp": 3, "api": 1, "ui": 0, "errors": 1, "users": 2}]
    out = U.fill_days(rows, 3, date(2026, 11, 10))
    assert [r["day"] for r in out] == ["2026-11-08", "2026-11-09", "2026-11-10"]
    assert out[0] == {"day": "2026-11-08", "mcp": 0, "api": 0, "ui": 0, "errors": 0, "users": 0}
    assert out[1]["mcp"] == 3
    assert len(U.fill_days([], 7, date(2026, 11, 10))) == 7


# ---------------------------------------------------------------- medidor de LLM

def test_medidor_soma_embedding_e_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_embedding(**_: Any) -> Any:
        resp = SimpleNamespace(data=[{"embedding": [0.1, 0.2]}], usage=SimpleNamespace(total_tokens=7))
        resp._hidden_params = {"response_cost": 0.001}
        return resp

    async def fake_rerank(**_: Any) -> Any:
        resp = SimpleNamespace(results=[{"index": 0, "relevance_score": 0.9}],
                               meta={"billed_units": {"total_tokens": 120}})
        resp._hidden_params = {"response_cost": 0.002}
        return resp

    monkeypatch.setattr(llm.litellm, "aembedding", fake_embedding)
    monkeypatch.setattr(llm.litellm, "arerank", fake_rerank)
    settings = SimpleNamespace(embed_model="m", embed_kwargs={}, rerank_model="r")

    async def run() -> U.LLMUsage:
        with llm.metered() as meter:
            await llm.embed_query(settings, "consulta")  # type: ignore[arg-type]
            await llm.embed_query(settings, "outra")  # type: ignore[arg-type]
            await llm.rerank(settings, "q", ["a"], 1)  # type: ignore[arg-type]
        # fora do bloco não conta
        await llm.embed_query(settings, "fora")  # type: ignore[arg-type]
        return meter.snapshot()

    usage = asyncio.run(run())
    assert usage.embed_tokens == 14
    assert usage.rerank_tokens == 120
    assert usage.cost_usd == pytest.approx(0.004)


def test_medidor_tolera_resposta_sem_uso(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_embedding(**_: Any) -> Any:
        return SimpleNamespace(data=[{"embedding": [0.1]}])

    monkeypatch.setattr(llm.litellm, "aembedding", fake_embedding)
    settings = SimpleNamespace(embed_model="m", embed_kwargs={})

    async def run() -> U.LLMUsage:
        with llm.metered() as meter:
            await llm.embed_query(settings, "x")  # type: ignore[arg-type]
        return meter.snapshot()

    assert asyncio.run(run()) == U.LLMUsage()
