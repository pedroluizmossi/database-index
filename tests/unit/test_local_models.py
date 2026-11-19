"""Modelos em servidor próprio (llama-swap/llama-server): endereço, prefixo da consulta e chave de memo."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from code_index.infra import llm
from code_index.settings import LOCAL_API_KEY, Settings


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_sem_servidor_proprio_nada_muda() -> None:
    s = _settings()
    assert s.embed_kwargs == {} and s.rerank_kwargs == {} and s.embed_query_prefix == ""


def test_servidor_proprio_endereco_e_chave_ficticia() -> None:
    s = _settings(EMBED_API_BASE="http://host:8090/v1", RERANK_API_BASE="http://host:8090/v1", EMBED_DIMENSIONS=1024)
    assert s.embed_kwargs == {"dimensions": 1024, "api_base": "http://host:8090/v1", "api_key": LOCAL_API_KEY}
    assert s.rerank_kwargs == {"api_base": "http://host:8090/v1", "api_key": LOCAL_API_KEY}


def test_vazio_e_nao_definido_e_quebra_de_linha_escapada() -> None:
    s = _settings(EMBED_API_BASE="", RERANK_API_BASE=" ", EMBED_QUERY_PREFIX="Instruct: achar código\\nQuery: ")
    assert s.embed_api_base is None and s.rerank_api_base is None
    assert s.embed_query_prefix == "Instruct: achar código\nQuery: "


def test_chave_de_memo_ignora_o_endereco() -> None:
    a = llm.make_embedder(_settings(EMBED_MODEL="openai/m", EMBED_API_BASE="http://localhost:8090/v1"))
    b = llm.make_embedder(_settings(EMBED_MODEL="openai/m", EMBED_API_BASE="http://host.minikube.internal:8090/v1"))
    assert a.__coco_memo_key__() == b.__coco_memo_key__() == ("openai/m", {})
    # sem servidor próprio, a mesma chave do LiteLLMEmbedder (o índice da Voyage não se invalida)
    v = llm.make_embedder(_settings())
    assert v.__coco_memo_key__() == ("voyage/voyage-code-4", {})


async def test_prefixo_so_na_consulta_e_rerank_com_endereco(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def aembedding(**kw: Any) -> SimpleNamespace:
        calls.append(kw)
        return SimpleNamespace(data=[{"embedding": [0.1, 0.2], "index": i} for i in range(len(kw["input"]))], usage=None)

    async def arerank(**kw: Any) -> SimpleNamespace:
        calls.append(kw)
        return SimpleNamespace(results=[{"index": 0, "relevance_score": 1.0}], meta={})

    monkeypatch.setattr(litellm, "aembedding", aembedding)
    monkeypatch.setattr(litellm, "arerank", arerank)
    s = _settings(EMBED_MODEL="openai/m", EMBED_API_BASE="http://h/v1", EMBED_QUERY_PREFIX="Q: ",
                  RERANK_MODEL="hosted_vllm/r", RERANK_API_BASE="http://h/v1")
    await llm.embed_query(s, "enviar email")
    await llm.embed_documents(s, ["class A {}"])
    await llm.rerank(s, "enviar email", ["a"], 1)
    assert calls[0]["input"] == ["Q: enviar email"] and calls[0]["api_base"] == "http://h/v1"
    assert calls[1]["input"] == ["class A {}"]           # trechos e textos guardados: sem prefixo
    assert calls[2]["api_base"] == "http://h/v1" and calls[2]["model"] == "hosted_vllm/r"


async def test_prefixo_de_documento_nfc_e_corte_matryoshka(monkeypatch: pytest.MonkeyPatch) -> None:
    import unicodedata

    import numpy as np

    calls: list[dict[str, Any]] = []

    async def aembedding(**kw: Any) -> SimpleNamespace:
        calls.append(kw)
        return SimpleNamespace(data=[{"embedding": [3.0, 4.0, 12.0, 0.0], "index": i} for i in range(len(kw["input"]))],
                               usage=None)

    monkeypatch.setattr(litellm, "aembedding", aembedding)
    s = _settings(EMBED_MODEL="openai/v", EMBED_API_BASE="http://h/v1", EMBED_QUERY_PREFIX="Q: ",
                  EMBED_DOCUMENT_PREFIX="D: ", EMBED_TRUNCATE_DIMS="2")
    emb = llm.make_embedder(s)
    nfd = unicodedata.normalize("NFD", "função")
    resp = await emb._aembedding_with_retry([nfd])
    assert calls[-1]["input"] == ["D: função"] and calls[-1]["input"][0] == unicodedata.normalize("NFC", "D: função")
    assert resp.data[0]["embedding"] == pytest.approx([0.6, 0.8])            # 2 dimensões, renormalizado
    q = await llm.embed_query(s, "x")
    assert calls[-1]["input"] == ["Q: x"] and np.allclose(q, [0.6, 0.8])
    (d,) = await llm.embed_documents(s, ["y"])
    assert calls[-1]["input"] == ["D: y"] and np.allclose(d, [0.6, 0.8])
    # prefixo e corte entram na chave de memo; sem eles, a chave de sempre
    assert emb.__coco_memo_key__() == ("openai/v", {}, ("document_prefix", "D: "), ("truncate_dims", 2))
    assert llm.make_embedder(_settings(EMBED_MODEL="openai/v")).__coco_memo_key__() == ("openai/v", {})


async def test_fechar_clientes_esvazia_o_cache() -> None:
    # o worker roda vários syncs no mesmo processo: depois de fechar, o próximo sync precisa de clientes novos
    litellm.in_memory_llm_clients_cache.set_cache("chave-de-teste", object())
    await llm.close_clients()
    assert litellm.in_memory_llm_clients_cache.get_cache("chave-de-teste") is None
