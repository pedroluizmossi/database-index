"""Chamadas a modelos via LiteLLM: embedding (Voyage por padrão), rerank e fechamento dos clientes.

`metered()` mede o consumo (tokens e custo estimado) das chamadas feitas dentro dele, na mesma tarefa
assíncrona: é como cada chamada de tool/rota sabe quanto gastou na Voyage. O indexador (CocoIndex) não
passa por aqui.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import litellm
import numpy as np
from cocoindex.ops.litellm import LiteLLMEmbedder

from code_index.domain.usage import LLMUsage
from code_index.settings import Settings


@dataclass
class _Meter:
    embed_tokens: int = 0
    rerank_tokens: int = 0
    cost_usd: float = 0.0

    def snapshot(self) -> LLMUsage:
        return LLMUsage(self.embed_tokens, self.rerank_tokens, round(self.cost_usd, 8))


_meter: ContextVar[_Meter | None] = ContextVar("code_index_llm_meter", default=None)


@contextmanager
def metered() -> Iterator[_Meter]:
    """Acumula o consumo das chamadas feitas dentro do bloco. Leia `.snapshot()` no fim."""
    meter = _Meter()
    reset = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(reset)


def _cost(resp: Any) -> float:
    hidden = getattr(resp, "_hidden_params", None)
    value = hidden.get("response_cost") if isinstance(hidden, dict) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _track_embedding(resp: Any) -> None:
    meter = _meter.get()
    if meter is None:
        return
    usage = getattr(resp, "usage", None)
    meter.embed_tokens += _int(getattr(usage, "total_tokens", None) or getattr(usage, "prompt_tokens", None))
    meter.cost_usd += _cost(resp)


def _track_rerank(resp: Any) -> None:
    meter = _meter.get()
    if meter is None:
        return
    meta = getattr(resp, "meta", None) or {}
    billed = (meta.get("billed_units") if isinstance(meta, dict) else None) or {}
    tokens = (meta.get("tokens") if isinstance(meta, dict) else None) or {}
    meter.rerank_tokens += _int(billed.get("total_tokens")) or _int(tokens.get("input_tokens"))
    meter.cost_usd += _cost(resp)


def make_embedder(settings: Settings) -> LiteLLMEmbedder:
    return LiteLLMEmbedder(
        settings.embed_model, max_inflight_requests=settings.embed_max_inflight, **settings.embed_kwargs
    )


async def embed_query(settings: Settings, text: str) -> np.ndarray:
    """Embedding de consulta (input_type=query: lado assimétrico do Voyage)."""
    resp = await litellm.aembedding(
        model=settings.embed_model, input=[text], input_type="query", **settings.embed_kwargs
    )
    _track_embedding(resp)
    return np.asarray(resp.data[0]["embedding"], dtype=np.float32)


def input_price(model: str) -> float | None:
    """US$ por token de entrada do modelo, pela tabela do LiteLLM (None se o modelo não está nela)."""
    try:
        price = litellm.model_cost.get(model, {}).get("input_cost_per_token")
    except Exception:  # noqa: BLE001 - tabela de preços é informativa
        return None
    return float(price) if isinstance(price, (int, float)) else None


async def embed_documents(settings: Settings, texts: list[str], *, batch_size: int = 128) -> list[np.ndarray]:
    """Embeddings de textos para guardar (input_type=document), em lotes. Fora do CocoIndex: perguntas dos
    resumos e cartões dos repos (domain/search_vectors)."""
    out: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        resp = await litellm.aembedding(
            model=settings.embed_model, input=batch, input_type="document", **settings.embed_kwargs
        )
        _track_embedding(resp)
        data = sorted(resp.data, key=lambda d: d["index"]) if all("index" in d for d in resp.data) else resp.data
        if len(data) != len(batch):
            raise RuntimeError(f"o provedor devolveu {len(data)} embeddings para {len(batch)} textos")
        out.extend(np.asarray(d["embedding"], dtype=np.float32) for d in data)
    return out


async def rerank(settings: Settings, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """Reordena os documentos. Devolve (índice original, score de relevância), do melhor para o pior."""
    resp = await litellm.arerank(model=settings.rerank_model, query=query, documents=documents, top_n=top_n)
    _track_rerank(resp)
    return [(int(r["index"]), float(r["relevance_score"])) for r in resp.results]


async def close_clients() -> None:
    """Fecha as sessões HTTP assíncronas do LiteLLM no loop atual (evita 'Task was destroyed')."""
    from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients

    await close_litellm_async_clients()
