"""Busca: híbrida (vetor + termos, fusão RRF ponderada no ChunkStore) seguida de rerank opcional.

Os termos usam a mesma tokenização de código da indexação (domain.search_text), e os pesos da
fusão dependem do tipo da consulta: frases favorecem o vetor; identificadores, o termo exato.
"""

from __future__ import annotations

import logging

from ..domain.models import Hit
from ..infra import llm
from ..domain.search_text import fusion_weights, query_kind, query_terms
from ..infra.chunk_store import ChunkStore
from ..settings import Settings

log = logging.getLogger(__name__)

_RERANK_DOC_CHARS = 6000
_MODES = ("hybrid", "semantic", "keyword")


class Searcher:
    def __init__(self, settings: Settings, chunks: ChunkStore) -> None:
        self._settings = settings
        self._chunks = chunks

    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        repos: list[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
        mode: str = "hybrid",
        rerank: bool = True,
        candidates: int | None = None,
    ) -> list[Hit]:
        """Busca trechos de código.

        mode: 'hybrid' (padrão), 'semantic' ou 'keyword'.
        rerank: reordena `candidates` resultados com `rerank_model` e devolve os `top_k` melhores.
        """
        if mode not in _MODES:
            raise ValueError(f"mode inválido: {mode}")
        s = self._settings
        use_vec = mode in ("hybrid", "semantic")
        use_kw = mode in ("hybrid", "keyword")
        use_rerank = rerank and bool(s.rerank_model)
        fetch_n = max(top_k, candidates or s.rerank_candidates) if use_rerank else top_k

        vec_weight, kw_weight = fusion_weights(query_kind(query))
        if mode == "semantic":
            kw_weight = 0.0
        elif mode == "keyword":
            vec_weight = 0.0
        query_vec = await llm.embed_query(s, query) if use_vec else None
        hits = await self._chunks.hybrid_search(
            query_vec=query_vec,
            terms=query_terms(query) if use_kw else [],
            vec_weight=vec_weight,
            kw_weight=kw_weight,
            limit=fetch_n,
            per_source=max(fetch_n * 2, 40),
            repos=repos,
            language=language,
            path_glob=path_glob,
        )
        if use_rerank and len(hits) > 1:
            try:
                return await self._rerank(query, hits, top_k)
            except Exception as e:  # noqa: BLE001 - rerank é melhoria; sem ele, fica a ordem híbrida
                log.warning("rerank falhou (%s); usando ordem da busca híbrida", e)
        return hits[:top_k]

    async def _rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        docs = [f"{h.repo}/{h.path}\n{h.code[:_RERANK_DOC_CHARS]}" for h in hits]
        ranked = await llm.rerank(self._settings, query, docs, top_k)
        out: list[Hit] = []
        for idx, score in ranked:
            hit = hits[idx]
            hit.score = round(float(score), 4)
            out.append(hit)
        return out
