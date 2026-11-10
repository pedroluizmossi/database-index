"""Busca: híbrida (vetor + termos, fusão RRF ponderada no ChunkStore) seguida de rerank opcional.

Os termos usam a mesma tokenização de código da indexação (domain.search_text), e os pesos da
fusão dependem do tipo da consulta: frases favorecem o vetor; identificadores, o termo exato.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections import OrderedDict

import numpy as np

from ..domain.models import Hit
from ..domain.ownership import Scope
from ..indexing.context import Decl, chunk_header
from ..infra.symbol_store import SymbolStore
from ..infra import llm
from ..domain.search_text import exact_terms, fusion_weights, query_kind, query_terms
from ..infra.chunk_store import KINDS, ChunkStore, kind_filter_conflict
from ..settings import Settings

log = logging.getLogger(__name__)

_RERANK_DOC_CHARS = 6000
_MODES = ("hybrid", "semantic", "keyword")
PER_FILE = 2  # trechos do mesmo arquivo por resposta (sobrepostos viram um só)
MERGED_MAX_CHARS = 4000  # teto de um trecho fundido
_EMBED_CACHE_SIZE = 256   # embeddings de consultas recentes (agents repetem buscas; a avaliação também)


RERANK_INSTRUCTION = (
    "Instrução: encontre o código-fonte que implementa o que a consulta pede; "
    "prefira código a documentação.\nConsulta: "
)


@dataclass(frozen=True)
class RerankOptions:
    """Como os candidatos vão para o reranker.

    instruction: prefixa a consulta com RERANK_INSTRUCTION (o reranker tende a preferir prosa).
    header: manda o mesmo cabeçalho do embedding (repo/caminho, classe envolvente, declarações).
    code_only: reordena só os trechos de código; a documentação fica nas posições da busca híbrida.
    skip_identifiers: consulta que parece um nome de código (query_kind == "identifier") não vai ao
        reranker: a busca por termos já acha o nome exato.
    """

    instruction: bool = False
    header: bool = False
    code_only: bool = False
    skip_identifiers: bool = False


def merge_code_ranking(hybrid: list[Hit], reranked_code: list[Hit]) -> list[Hit]:
    """Docs nas mesmas posições da ordem híbrida; as posições de código recebem o código reordenado."""
    ranked = {id(h) for h in reranked_code}
    rest = [h for h in hybrid if h.kind != "doc" and id(h) not in ranked]  # o reranker devolveu menos
    code = iter([*reranked_code, *rest])
    return [h if h.kind == "doc" else next(code) for h in hybrid]


def _lines(h: Hit) -> dict[int, str]:
    return {h.start_line + i: text for i, text in enumerate(h.code.split("\n"))}


def default_max_docs(top_k: int) -> int:
    """Páginas de documentação por resposta quando a busca mistura código e docs."""
    return max(2, top_k // 4)


def diversify(hits: list[Hit], top_k: int, per_file: int = PER_FILE, max_docs: int | None = None) -> list[Hit]:
    """Até `top_k` resultados, na ordem recebida, sem repetir o mesmo arquivo demais.

    Trecho que se sobrepõe (ou encosta) a outro já escolhido do mesmo arquivo é fundido nele
    (o chunking usa sobreposição, então vizinhos repetem linhas). Cada arquivo entra no máximo
    `per_file` vezes; os lugares liberados vão para os próximos candidatos.
    Trecho idêntico a um já escolhido, de outro repo (pom.xml, configs copiadas), não ocupa
    lugar: o repo vai para `also_in` do primeiro. `max_docs` limita as páginas de documentação
    (None = sem limite), para a wiki não tomar o lugar do código.
    """
    out: list[Hit] = []
    by_file: dict[tuple[str, str], list[Hit]] = {}
    by_code: dict[str, Hit] = {}
    docs = 0
    for h in hits:
        if len(out) >= top_k:
            break
        twin = by_code.get(h.code.strip())
        if twin is not None and twin.repo != h.repo:
            if h.repo not in twin.also_in:
                twin.also_in.append(h.repo)
            continue
        same = by_file.setdefault((h.repo, h.path), [])
        near = next((o for o in same if h.start_line <= o.end_line + 1 and o.start_line <= h.end_line + 1), None)
        if near is not None:
            merged = {**_lines(h), **_lines(near)}
            code = "\n".join(merged.get(i, "") for i in range(min(merged), max(merged) + 1))
            if len(code) <= MERGED_MAX_CHARS:  # senão, o vizinho só é descartado (já está quase todo ali)
                near.start_line, near.end_line, near.code = min(merged), max(merged), code
            continue
        if len(same) >= per_file:
            continue
        if h.kind == "doc" and max_docs is not None:
            if docs >= max_docs:
                continue
            docs += 1
        same.append(h)
        by_code.setdefault(h.code.strip(), h)
        out.append(h)
    return out


class Searcher:
    def __init__(self, settings: Settings, chunks: ChunkStore, symbols: SymbolStore | None = None) -> None:
        self._settings = settings
        self._chunks = chunks
        self._symbols = symbols
        self._embed_cache: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()

    async def _embed_query(self, query: str) -> np.ndarray:
        key = (self._settings.embed_model, query)
        vec = self._embed_cache.get(key)
        if vec is None:
            vec = await llm.embed_query(self._settings, query)
            self._embed_cache[key] = vec
            if len(self._embed_cache) > _EMBED_CACHE_SIZE:
                self._embed_cache.popitem(last=False)
        else:
            self._embed_cache.move_to_end(key)
        return vec

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
        kind: str | None = None,
        include_linked_docs: bool = True,
        exclude_repos: list[str] | None = None,
        exclude_path_glob: list[str] | None = None,
        weights: tuple[float, float] | None = None,
        max_docs: int | None = None,
        rerank_options: RerankOptions | None = None,
        scope: Scope | None = None,
    ) -> list[Hit]:
        """Busca trechos de código e documentação.

        mode: 'hybrid' (padrão), 'semantic' ou 'keyword'.
        kind: 'code', 'doc' ou None (ambos). include_linked_docs: com `repos`, inclui páginas de
        documentação externa ligadas a eles.
        rerank: reordena `candidates` resultados com `rerank_model` e devolve os `top_k` melhores.
        weights: (vetor, termos) da fusão no lugar dos padrões por tipo de consulta (avaliação).
        rerank_options: variações do rerank (padrão: as do Settings, RERANK_INSTRUCTION etc.).
        scope: só o código de um domínio/serviço do DDD (repos e pastas; domain/ownership.scope_for).
        max_docs: páginas de documentação no resultado quando `kind` é vazio (padrão:
        default_max_docs(top_k)); com kind='doc' não há limite.
        """
        if not query or not query.strip():
            raise ValueError("consulta vazia")
        if mode not in _MODES:
            raise ValueError(f"mode inválido: {mode}")
        if kind is not None and kind not in KINDS:
            raise ValueError(f"kind inválido: {kind} (use code ou doc)")
        if conflict := kind_filter_conflict(kind, language, path_glob):
            raise ValueError(conflict)
        s = self._settings
        use_vec = mode in ("hybrid", "semantic")
        use_kw = mode in ("hybrid", "keyword")
        opts = rerank_options or self.default_rerank_options()
        use_rerank = rerank and bool(s.rerank_model) and not (opts.skip_identifiers and query_kind(query) == "identifier")
        fetch_n = max(top_k, candidates or s.rerank_candidates) if use_rerank else max(top_k * 3, 30)

        vec_weight, kw_weight = weights or fusion_weights(query_kind(query))
        if mode == "semantic":
            kw_weight = 0.0
        elif mode == "keyword":
            vec_weight = 0.0
        try:
            query_vec = await self._embed_query(query) if use_vec else None
        except Exception as e:  # noqa: BLE001 - erro do provedor vira mensagem clara para CLI/API/MCP
            log.warning("embedding da consulta falhou: %s", e)
            raise RuntimeError(
                f"não foi possível gerar o embedding da consulta ({type(e).__name__}); "
                "tente mode='keyword' ou mais tarde"
            ) from None
        hits = await self._chunks.hybrid_search(
            query_vec=query_vec,
            terms=query_terms(query) if use_kw else [],
            exact_terms=exact_terms(query) if use_kw else [],
            vec_weight=vec_weight,
            kw_weight=kw_weight,
            limit=fetch_n,
            per_source=max(fetch_n * 2, 40),
            repos=repos,
            language=language,
            path_glob=path_glob,
            kind=kind,
            include_linked_docs=include_linked_docs,
            exclude_repos=exclude_repos,
            exclude_path_glob=exclude_path_glob,
            scope=scope,
        )
        if use_rerank and len(hits) > 1:
            try:
                # reordena todos (o custo é pelo texto enviado, não pelo top_n) para sobrar
                # candidato quando diversify descartar repetidos
                if kind == "doc":  # pedir "prefira código" numa busca só de documentação não faz sentido
                    opts = RerankOptions(instruction=False, header=opts.header)
                hits = await self._rerank(query, hits, opts)
            except Exception as e:  # noqa: BLE001 - rerank é melhoria; sem ele, fica a ordem híbrida
                log.warning("rerank falhou (%s); usando ordem da busca híbrida", e)
        if kind is None and max_docs is None:
            max_docs = default_max_docs(top_k)
        return diversify(hits, top_k, max_docs=None if kind == "doc" else max_docs)

    def default_rerank_options(self) -> RerankOptions:
        s = self._settings
        return RerankOptions(s.rerank_instruction, s.rerank_header, s.rerank_code_only, s.rerank_skip_identifiers)

    async def _rerank_texts(self, hits: list[Hit], header: bool) -> list[str]:
        if not (header and self._symbols):
            return [f"{h.repo}/{h.path}{f' — {h.title}' if h.title else ''}\n{h.code[:_RERANK_DOC_CHARS]}" for h in hits]
        decls = await self._symbols.declarations([(h.repo, h.path) for h in hits])
        texts = []
        for h in hits:
            file_decls = [Decl(line, kind, name) for line, kind, name in decls.get((h.repo, h.path), [])]  # type: ignore[arg-type]
            head = chunk_header(h.repo, h.path, h.language, file_decls, h.start_line, h.end_line)
            if h.title:
                head += f"\n# página: {h.title}"
            texts.append(f"{head}\n{h.code[:_RERANK_DOC_CHARS]}")
        return texts

    async def _rerank(self, query: str, hits: list[Hit], opts: RerankOptions) -> list[Hit]:
        """Reordena todos os candidatos (o custo é pelo texto enviado, não pelo top_n), para sobrar
        candidato quando diversify descartar repetidos."""
        targets = [h for h in hits if h.kind == "code"] if opts.code_only else hits
        if len(targets) <= 1:
            return hits
        texts = await self._rerank_texts(targets, opts.header)
        ranked = await llm.rerank(self._settings, (RERANK_INSTRUCTION + query) if opts.instruction else query,
                                  texts, len(texts))
        out: list[Hit] = []
        for idx, score in ranked:
            hit = targets[idx]
            hit.score = round(float(score), 4)
            out.append(hit)
        return merge_code_ranking(hits, out) if opts.code_only else out
