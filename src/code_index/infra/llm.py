"""Chamadas a modelos via LiteLLM: embedding (Voyage por padrão), rerank e fechamento dos clientes."""

from __future__ import annotations

import litellm
import numpy as np
from cocoindex.ops.litellm import LiteLLMEmbedder

from code_index.settings import Settings


def make_embedder(settings: Settings) -> LiteLLMEmbedder:
    return LiteLLMEmbedder(
        settings.embed_model, max_inflight_requests=settings.embed_max_inflight, **settings.embed_kwargs
    )


async def embed_query(settings: Settings, text: str) -> np.ndarray:
    """Embedding de consulta (input_type=query: lado assimétrico do Voyage)."""
    resp = await litellm.aembedding(
        model=settings.embed_model, input=[text], input_type="query", **settings.embed_kwargs
    )
    return np.asarray(resp.data[0]["embedding"], dtype=np.float32)


async def rerank(settings: Settings, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """Reordena os documentos. Devolve (índice original, score de relevância), do melhor para o pior."""
    resp = await litellm.arerank(model=settings.rerank_model, query=query, documents=documents, top_n=top_n)
    return [(int(r["index"]), float(r["relevance_score"])) for r in resp.results]


async def close_clients() -> None:
    """Fecha as sessões HTTP assíncronas do LiteLLM no loop atual (evita 'Task was destroyed')."""
    from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients

    await close_litellm_async_clients()
