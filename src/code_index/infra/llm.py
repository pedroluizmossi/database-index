"""Chamadas a modelos via LiteLLM: embedding (Voyage por padrão), rerank e fechamento dos clientes.

`metered()` mede o consumo (tokens e custo estimado) das chamadas feitas dentro dele, na mesma tarefa
assíncrona: é como cada chamada de tool/rota sabe quanto gastou na Voyage. O indexador (CocoIndex) não
passa por aqui.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator, Sequence
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


def _truncate(vector: Sequence[float], dims: int | None) -> list[float]:
    """Primeiras `dims` dimensões, renormalizadas (Matryoshka). Sem `dims` (ou vetor menor), como veio."""
    if not dims or len(vector) <= dims:
        return list(vector)
    head = np.asarray(vector[:dims], dtype=np.float32)
    norm = float(np.linalg.norm(head))
    return (head / norm if norm else head).tolist()


def _input_text(prefix: str, text: str, local: bool) -> str:
    """Texto enviado ao modelo: prefixo de instrução + texto. Servidor próprio (llama.cpp) não normaliza o Unicode
    como o tokenizer original: NFC aqui, para o mesmo texto dar o mesmo vetor (ex.: nomes vindos do macOS, NFD)."""
    out = prefix + text
    return unicodedata.normalize("NFC", out) if local else out


class _Embedder(LiteLLMEmbedder):
    """LiteLLMEmbedder com três ajustes para modelos em servidor próprio:

    - a chave de memo ignora o endereço e a chave do servidor (`EMBED_API_BASE`): mudam por ambiente (localhost,
      cluster) sem mudar o vetor;
    - `EMBED_DOCUMENT_PREFIX`: instrução antes de cada documento (voyage-4-nano), com NFC no servidor próprio;
    - `EMBED_TRUNCATE_DIMS`: vetor cortado e renormalizado (Matryoshka), também no tamanho da coluna.

    Sem prefixo e sem corte, a chave de memo é a mesma do LiteLLMEmbedder: o índice da Voyage não se invalida."""

    def __init__(self, model: str, *, document_prefix: str = "", truncate_dims: int | None = None,
                 local: bool = False, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self._document_prefix = document_prefix
        self._truncate_dims = truncate_dims
        self._local = local

    async def _aembedding_with_retry(self, texts: list[str], **extra: Any) -> Any:
        inputs = [_input_text(self._document_prefix, t, self._local) for t in texts]
        response = await super()._aembedding_with_retry(inputs, **extra)
        if self._truncate_dims:
            for item in response.data:
                item["embedding"] = _truncate(item["embedding"], self._truncate_dims)
        return response

    def __coco_memo_key__(self) -> object:
        key: tuple[Any, ...] = (self._model, {k: v for k, v in self._kwargs.items() if k not in ("api_base", "api_key")})
        if self._document_prefix:
            key += (("document_prefix", self._document_prefix),)
        if self._truncate_dims:
            key += (("truncate_dims", self._truncate_dims),)
        return key


def make_embedder(settings: Settings) -> LiteLLMEmbedder:
    return _Embedder(
        settings.embed_model, document_prefix=settings.embed_document_prefix,
        truncate_dims=settings.embed_truncate_dims, local=settings.embed_api_base is not None,
        max_inflight_requests=settings.embed_max_inflight, **settings.embed_kwargs,
    )


async def embed_query(settings: Settings, text: str) -> np.ndarray:
    """Embedding de consulta (input_type=query: lado assimétrico do Voyage; `EMBED_QUERY_PREFIX`: a instrução dos
    modelos que a pedem no texto, como o Qwen3-Embedding)."""
    local = settings.embed_api_base is not None
    resp = await litellm.aembedding(
        model=settings.embed_model, input=[_input_text(settings.embed_query_prefix, text, local)], input_type="query",
        **settings.embed_kwargs,
    )
    _track_embedding(resp)
    return np.asarray(_truncate(resp.data[0]["embedding"], settings.embed_truncate_dims), dtype=np.float32)


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
        local = settings.embed_api_base is not None
        resp = await litellm.aembedding(
            model=settings.embed_model, input=[_input_text(settings.embed_document_prefix, t, local) for t in batch],
            input_type="document", **settings.embed_kwargs,
        )
        _track_embedding(resp)
        data = sorted(resp.data, key=lambda d: d["index"]) if all("index" in d for d in resp.data) else resp.data
        if len(data) != len(batch):
            raise RuntimeError(f"o provedor devolveu {len(data)} embeddings para {len(batch)} textos")
        out.extend(np.asarray(_truncate(d["embedding"], settings.embed_truncate_dims), dtype=np.float32) for d in data)
    return out


async def rerank(settings: Settings, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """Reordena os documentos. Devolve (índice original, score de relevância), do melhor para o pior."""
    resp = await litellm.arerank(model=settings.rerank_model, query=query, documents=documents, top_n=top_n,
                                 **settings.rerank_kwargs)
    _track_rerank(resp)
    return [(int(r["index"]), float(r["relevance_score"])) for r in resp.results]


async def close_clients() -> None:
    """Fecha as sessões HTTP assíncronas do LiteLLM no loop atual (evita 'Task was destroyed') e esvazia o cache de
    clientes. Sem esvaziar, o próximo sync no mesmo processo (worker) reaproveitava o cliente fechado dos provedores
    OpenAI-compatíveis (servidor próprio, EMBED_API_BASE) e toda chamada falhava com "the client has been closed"."""
    from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients

    await close_litellm_async_clients()
    cache = getattr(litellm, "in_memory_llm_clients_cache", None)
    if cache is not None and hasattr(cache, "flush_cache"):
        cache.flush_cache()
