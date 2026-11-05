"""Embeddings locais: parâmetros repassados ao LiteLLM e o servidor no formato da API da Voyage (modelo falso)."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from code_index.infra.local_embedder import check_dimension, normalize
from code_index.interfaces.embed_server import create_embed_app


class FakeEmbedder:
    model_name = "MongoDB/voyage-4-nano"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None, int]] = []

    def embed(self, texts, input_type, dimension):
        self.calls.append((texts, input_type, dimension))
        base = np.arange(1, dimension + 1, dtype=np.float32)
        return normalize(np.stack([base * (i + 1) for i in range(len(texts))])), 7 * len(texts)


@pytest.fixture
def server(make_settings):
    fake = FakeEmbedder()
    app = create_embed_app(make_settings(EMBED_API_KEY="segredo-local"), fake)
    return TestClient(app), fake


AUTH = {"Authorization": "Bearer segredo-local"}


def test_sem_servidor_local_so_repassa_dimensao(make_settings) -> None:
    assert make_settings().embed_kwargs == {}
    assert make_settings(EMBED_DIMENSIONS=1024).embed_kwargs == {"dimensions": 1024}


def test_com_servidor_local_manda_a_chave_local_e_nunca_a_da_voyage(make_settings) -> None:
    s = make_settings(EMBED_API_BASE="http://127.0.0.1:8766/v1", EMBED_DIMENSIONS=1024)
    assert s.embed_kwargs == {"dimensions": 1024, "api_base": "http://127.0.0.1:8766/v1", "api_key": "local"}
    s = make_settings(EMBED_API_BASE="http://127.0.0.1:8766/v1", EMBED_API_KEY="outra")
    assert s.embed_kwargs["api_key"] == "outra"


def test_dimensoes_aceitas_sao_as_do_treino() -> None:
    assert check_dimension(None) == 1024
    assert check_dimension(256) == 256
    with pytest.raises(ValueError, match="output_dimension"):
        check_dimension(768)


def test_normaliza_depois_do_corte() -> None:
    v = normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert np.allclose(v[0], [0.6, 0.8])
    assert np.allclose(v[1], [0.0, 0.0])  # vetor nulo não vira NaN


def test_resposta_no_formato_da_voyage(server) -> None:
    client, fake = server
    r = client.post("/v1/embeddings", headers=AUTH, json={
        "input": ["def a(): pass", "class B: ..."], "model": "voyage-4-nano",
        "input_type": "document", "output_dimension": 256,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert len(body["data"][0]["embedding"]) == 256
    assert body["usage"] == {"total_tokens": 14}
    assert fake.calls == [(["def a(): pass", "class B: ..."], "document", 256)]


def test_consulta_usa_o_prompt_de_consulta_e_texto_unico(server) -> None:
    client, fake = server
    r = client.post("/embeddings", headers=AUTH, json={"input": "onde valida o JWT?", "input_type": "query"})
    assert r.status_code == 200
    assert fake.calls == [(["onde valida o JWT?"], "query", 1024)]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer errada"}, {"Authorization": "segredo-local"}])
def test_exige_a_chave(server, headers) -> None:
    client, fake = server
    r = client.post("/v1/embeddings", headers=headers, json={"input": "x"})
    assert r.status_code == 401
    assert fake.calls == []


@pytest.mark.parametrize("payload", [
    {"input": []},
    {"input": ["x"], "output_dimension": 768},
    {"input": ["x"] * 1001},
    {"input": ["x"], "input_type": "code"},
])
def test_rejeita_entrada_invalida(server, payload) -> None:
    client, fake = server
    assert client.post("/v1/embeddings", headers=AUTH, json=payload).status_code in (400, 422)
    assert fake.calls == []
