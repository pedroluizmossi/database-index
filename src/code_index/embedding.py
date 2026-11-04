from __future__ import annotations

import litellm
import numpy as np
from cocoindex.ops.litellm import LiteLLMEmbedder

from .config import EMBED_DIMENSIONS, EMBED_MODEL

_EXTRA = {"dimensions": EMBED_DIMENSIONS} if EMBED_DIMENSIONS else {}


def make_embedder() -> LiteLLMEmbedder:
    return LiteLLMEmbedder(EMBED_MODEL, max_inflight_requests=4, **_EXTRA)


async def embed_query(text: str) -> np.ndarray:
    """Embedding de consulta (input_type=query: lado assimétrico do Voyage)."""
    resp = await litellm.aembedding(model=EMBED_MODEL, input=[text], input_type="query", **_EXTRA)
    return np.asarray(resp.data[0]["embedding"], dtype=np.float32)


async def close_litellm_clients() -> None:
    """Fecha as sessões HTTP assíncronas do LiteLLM no loop atual (evita 'Task was destroyed')."""
    from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients

    await close_litellm_async_clients()
