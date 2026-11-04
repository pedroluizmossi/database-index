"""Consultas sobre a tabela de chunks (gerenciada pelo CocoIndex): busca híbrida e contagens.

A busca combina vetor (HNSW, cosseno) e full-text ('simple') por RRF (k=60). Os valores
entram sempre como parâmetros; só os nomes de tabela vêm das configurações.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import asyncpg
import numpy as np

from code_index.domain.models import Hit
from code_index.settings import Settings

_RRF_K = 60

_STOPWORDS = frozenset("""
a o e é de da do das dos em no na nos nas um uma uns umas para por com sem que se como onde qual quais
ao aos à às ou mais menos ser está são foi feito feita sobre entre seu sua seus suas isso este esta
the an and or of to in on for with from by is are be was how where what which when does do this that
""".split())


def keyword_tsquery(query: str) -> str | None:
    """Termos significativos unidos por OR: frases em linguagem natural ainda casam
    parcialmente, e o ts_rank prioriza trechos que contêm mais termos."""
    terms: list[str] = []
    for t in re.findall(r"\w+", query.lower()):
        if len(t) >= 3 and t not in _STOPWORDS and t not in terms:
            terms.append(t)
    return " | ".join(terms) or None


def glob_to_like(glob: str) -> str:
    """Converte um glob de caminho ('src/**/*.py') em padrão LIKE do Postgres."""
    escaped = glob.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return escaped.replace("**", "*").replace("*", "%").replace("?", "_")


class _Params:
    """Numeração dinâmica de parâmetros ($1, $2, ...) para montar SQL com filtros opcionais."""

    def __init__(self) -> None:
        self.values: list[object] = []

    def __call__(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


def _filters(p: _Params, repos: Sequence[str] | None, language: str | None, path_glob: str | None) -> str:
    clauses = []
    if repos:
        clauses.append(f"repo = ANY({p(list(repos))})")
    if language:
        clauses.append(f"language = {p(language)}")
    if path_glob:
        clauses.append(f"path LIKE {p(glob_to_like(path_glob))}")
    return " AND ".join(clauses) or "TRUE"


class ChunkStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._table = settings.chunks_table_qualified

    async def hybrid_search(
        self,
        *,
        query_vec: np.ndarray | None,
        tsquery: str | None,
        limit: int,
        per_source: int,
        repos: Sequence[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
    ) -> list[Hit]:
        """Fusão RRF das fontes disponíveis: vetor (se `query_vec`) e full-text (se `tsquery`).

        `per_source` é quantos candidatos cada fonte traz; `limit` é quantos sobram após a fusão.
        Sem nenhuma fonte, devolve lista vazia.
        """
        if query_vec is None and not tsquery:
            return []
        p = _Params()
        k = p(per_source)
        where = _filters(p, repos, language, path_glob)
        t = self._table

        parts: list[str] = []
        if query_vec is not None:
            v = p(query_vec)
            parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY embedding <=> {v}) AS rnk
            FROM (SELECT repo, path, id, embedding FROM {t} WHERE {where}
                  ORDER BY embedding <=> {v} LIMIT {k}) v""")
        if tsquery:
            q = p(tsquery)
            parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY rank DESC) AS rnk
            FROM (SELECT repo, path, id,
                         ts_rank(to_tsvector('simple', code), to_tsquery('simple', {q})) AS rank
                  FROM {t}
                  WHERE to_tsvector('simple', code) @@ to_tsquery('simple', {q}) AND {where}
                  ORDER BY rank DESC LIMIT {k}) k""")
        sql = f"""
        WITH ranked AS ({" UNION ALL ".join(parts)}),
        fused AS (SELECT repo, path, id, SUM(1.0 / ({_RRF_K} + rnk)) AS score
                  FROM ranked GROUP BY repo, path, id)
        SELECT t.repo, t.path, t.language, t.start_line, t.end_line, t.code, f.score
        FROM fused f JOIN {t} t USING (repo, path, id)
        ORDER BY f.score DESC LIMIT {int(limit)}"""

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    if query_vec is not None:
                        # pgvector >= 0.8: continua varrendo o HNSW quando filtros descartam candidatos.
                        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                    rows = await conn.fetch(sql, *p.values)
        except asyncpg.UndefinedTableError:  # nada indexado ainda (a tabela nasce no 1º sync)
            return []

        # Score normalizado pelo máximo possível com as fontes usadas (1 fonte: 1/61; 2 fontes: 2/61).
        max_score = len(parts) / (_RRF_K + 1)
        return [
            Hit(
                repo=r["repo"], path=r["path"], language=r["language"],
                start_line=r["start_line"], end_line=r["end_line"],
                score=round(float(r["score"]) / max_score, 4), code=r["code"],
            )
            for r in rows
        ]

    async def counts_by_repo(self) -> dict[str, tuple[int, int]]:
        """Por repo: (arquivos distintos, chunks). Tabela inexistente (nada indexado ainda) → {}."""
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"SELECT repo, COUNT(DISTINCT path) AS f, COUNT(*) AS c FROM {self._table} GROUP BY repo"
                )
            except asyncpg.UndefinedTableError:
                return {}
        return {r["repo"]: (r["f"], r["c"]) for r in rows}
