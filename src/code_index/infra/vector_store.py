"""Vetores extras da busca (`search_vectors`): perguntas e texto dos resumos e cartões dos repos.

Lê os textos de origem (`file_summaries`, `repo_cards.card`), o que já foi gravado e grava/apaga vetores. A busca
não passa por aqui: o ChunkStore monta o SQL (mesma consulta da fusão). A coluna `embedding` não fixa a dimensão
(o modelo pode mudar); para o HNSW, cada modelo ganha um índice parcial sobre `embedding::vector(N)`
(`ensure_index`), e a busca usa a mesma expressão (`index_expr`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import asyncpg
import numpy as np

from ..domain.search_vectors import (
    FILE_KINDS, Stored, VectorKey, VectorText, card_texts, plan, question_texts, summary_texts,
)
from ..settings import Settings

def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def index_expr(column: str, dims: int) -> str:
    """Expressão indexada (e usada na busca para o planner escolher o índice)."""
    return f"({column}::vector({int(dims)}))"


def index_predicate(model: str) -> str:
    """Predicado do índice parcial: a consulta precisa repetir esses literais para o planner usá-lo."""
    kinds = ", ".join(sql_literal(k) for k in FILE_KINDS)
    return f"model = {sql_literal(model)} AND kind IN ({kinds})"


def index_name(model: str, dims: int) -> str:
    return f"search_vectors_hnsw_{hashlib.sha1(f'{model}:{dims}'.encode()).hexdigest()[:12]}"


# Uma geração por vez, entre processos (servidor, worker, CLI): o lote é idempotente, mas pagaria duas vezes.
_LOCK_KEY = "code_index.search_vectors"


class VectorStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        schema = settings.pg_schema
        self.table = f'"{schema}".search_vectors'
        self._summaries = f'"{schema}".file_summaries'
        self._cards = f'"{schema}".repo_cards'

    async def sources(self, repo: str | None = None) -> list[VectorText]:
        """Textos que deveriam ter vetor: o resumo e cada pergunta de cada arquivo e o cartão de cada repo
        (só de `repo`, se informado)."""
        out: list[VectorText] = []
        summaries = await self._pool.fetch(
            f"SELECT repo, path, summary, questions, keywords FROM {self._summaries} "
            "WHERE $1::text IS NULL OR repo = $1 ORDER BY repo, path", repo)
        for r in summaries:
            out.extend(summary_texts(r["repo"], r["path"], r["summary"], json.loads(r["keywords"]) or []))
            out.extend(question_texts(r["repo"], r["path"], json.loads(r["questions"]) or []))
        cards = await self._pool.fetch(f"SELECT repo, card FROM {self._cards} WHERE card IS NOT NULL "
                                       "AND ($1::text IS NULL OR repo = $1) ORDER BY repo", repo)
        for r in cards:
            card = json.loads(r["card"]) if isinstance(r["card"], str) else r["card"]
            out.extend(card_texts(r["repo"], card))
        return out

    async def stored(self, repo: str | None = None) -> dict[VectorKey, Stored]:
        rows = await self._pool.fetch(
            f"SELECT kind, repo, path, ord, text_hash, model FROM {self.table} WHERE $1::text IS NULL OR repo = $1", repo)
        return {VectorKey(r["kind"], r["repo"], r["path"], r["ord"]): Stored(r["text_hash"], r["model"]) for r in rows}

    async def upsert(self, items: Sequence[VectorText], vectors: Sequence[np.ndarray], model: str) -> None:
        if len(items) != len(vectors):
            raise ValueError("número de vetores diferente do de textos")
        await self._pool.executemany(
            f"""INSERT INTO {self.table} (kind, repo, path, ord, text, text_hash, model, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (kind, repo, path, ord) DO UPDATE SET text = EXCLUDED.text,
                    text_hash = EXCLUDED.text_hash, model = EXCLUDED.model, embedding = EXCLUDED.embedding,
                    embedded_at = now()""",
            [(i.key.kind, i.key.repo, i.key.path, i.key.ord, i.text, i.hash, model, v)
             for i, v in zip(items, vectors)],
        )

    async def delete(self, keys: Sequence[VectorKey]) -> int:
        if not keys:
            return 0
        res = await self._pool.execute(
            f"""DELETE FROM {self.table} t USING unnest($1::text[], $2::text[], $3::text[], $4::int[]) AS k(kind, repo, path, ord)
                WHERE t.kind = k.kind AND t.repo = k.repo AND t.path = k.path AND t.ord = k.ord""",
            [k.kind for k in keys], [k.repo for k in keys], [k.path for k in keys], [k.ord for k in keys],
        )
        return int(res.split()[-1])

    async def pending_files(self, model: str, repo: str | None = None) -> set[tuple[str, str]]:
        """Arquivos (repo, caminho) com resumo cujos vetores (resumo ou perguntas) não estão em dia com `model`."""
        p = plan(await self.sources(repo), await self.stored(repo), model)
        return {(i.key.repo, i.key.path) for i in p.embed if i.key.kind in FILE_KINDS}

    async def stale_summaries(self) -> int:
        """Resumos desatualizados (o arquivo mudou depois deles): os vetores continuam, com voto reduzido."""
        try:
            return int(await self._pool.fetchval(f"SELECT count(*) FROM {self._summaries} WHERE stale"))
        except asyncpg.UndefinedColumnError:
            return 0

    async def dims(self, model: str) -> int | None:
        """Dimensão dos vetores gravados com `model` (None se não há nenhum)."""
        return await self._pool.fetchval(
            f"SELECT vector_dims(embedding) FROM {self.table} WHERE model = $1 LIMIT 1", model)

    async def ensure_index(self, model: str) -> str | None:
        """Índice HNSW parcial para os vetores de arquivo (resumo e perguntas) do modelo. Idempotente; devolve o
        nome do índice (None se não há vetores do modelo)."""
        dims = await self.dims(model)
        if not dims:
            return None
        name = index_name(model, dims)
        await self._pool.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {self.table} USING hnsw ({index_expr('embedding', dims)} "
            f"vector_cosine_ops) WHERE {index_predicate(model)}")
        return name

    async def last_embedded_at(self) -> Any:
        return await self._pool.fetchval(f"SELECT max(embedded_at) FROM {self.table}")

    async def try_lock(self) -> asyncpg.pool.PoolConnectionProxy | None:
        """Conexão que detém a trava da geração, ou None se outra geração está rodando. Libere com `unlock`."""
        conn = await self._pool.acquire()
        try:
            if await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", _LOCK_KEY):
                return conn
        except BaseException:
            await self._pool.release(conn)
            raise
        await self._pool.release(conn)
        return None

    async def unlock(self, conn: asyncpg.pool.PoolConnectionProxy) -> None:
        try:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", _LOCK_KEY)
        finally:
            await self._pool.release(conn)
