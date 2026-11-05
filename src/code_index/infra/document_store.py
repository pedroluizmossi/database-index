"""Catálogo de documentos das fontes externas (tabela `documents`): tipo, atributos e páginas ignoradas.

Preenchido pelo plugin de cada fonte (`sources/`) a partir do que ele materializou em disco, sem LLM e
sem Voyage. O pipeline do CocoIndex continua gravando `doc_pages` (o que foi indexado); aqui fica o que
existe na fonte e o que cada documento é.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import asyncpg

from ..domain.documents import API_OPERATION, DocumentMeta
from ..settings import Settings


class DocumentStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        self.table = f'"{settings.pg_schema}".documents'

    # ------------------------------------------------------------ escrita (plugins de fonte)

    async def replace_source(self, doc_repo: str, docs: Sequence[DocumentMeta]) -> dict[str, int]:
        """Espelha o catálogo de uma fonte: grava/atualiza os documentos e marca os que sumiram.
        Documento que sumiu é apagado, salvo se estiver ignorado (a marca de ignorado é preservada)."""
        rows = [(doc_repo, d.path, d.type, d.title, d.url, json.dumps(list(d.tags), ensure_ascii=False),
                 d.updated_at, json.dumps(d.attrs, ensure_ascii=False, sort_keys=True)) for d in docs]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"UPDATE {self.table} SET present = false WHERE doc_repo = $1", doc_repo)
                if rows:
                    await conn.executemany(
                        f"""INSERT INTO {self.table} (doc_repo, path, type, title, url, tags, source_version, attrs,
                                                       present, refreshed_at)
                            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, true, now())
                            ON CONFLICT (doc_repo, path) DO UPDATE SET type = EXCLUDED.type, title = EXCLUDED.title,
                                url = EXCLUDED.url, tags = EXCLUDED.tags, source_version = EXCLUDED.source_version,
                                attrs = EXCLUDED.attrs, present = true, refreshed_at = now()""",
                        rows,
                    )
                deleted = await conn.execute(
                    f"DELETE FROM {self.table} WHERE doc_repo = $1 AND NOT present AND NOT ignored", doc_repo)
        return {"documents": len(rows), "removed": int(deleted.split()[-1])}

    async def drop_source(self, doc_repo: str) -> None:
        """Fonte removida: os documentos saem do catálogo (inclusive marcas de ignorado)."""
        await self._pool.execute(f"DELETE FROM {self.table} WHERE doc_repo = $1", doc_repo)

    async def prune_sources(self, keep: Iterable[str]) -> int:
        """Apaga documentos de fontes que não existem mais."""
        res = await self._pool.execute(f"DELETE FROM {self.table} WHERE NOT (doc_repo = ANY($1::text[]))", list(keep))
        return int(res.split()[-1])

    # ------------------------------------------------------------ páginas ignoradas

    async def set_ignore(self, doc_repo: str, path: str, reason: str) -> None:
        await self._pool.execute(
            f"""INSERT INTO {self.table} (doc_repo, path, present, ignored, ignore_reason) VALUES ($1, $2, false, true, $3)
                ON CONFLICT (doc_repo, path) DO UPDATE SET ignored = true, ignore_reason = EXCLUDED.ignore_reason""",
            doc_repo, path, reason,
        )

    async def remove_ignore(self, doc_repo: str, path: str) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                res = await conn.execute(
                    f"UPDATE {self.table} SET ignored = false, ignore_reason = '' "
                    "WHERE doc_repo = $1 AND path = $2 AND ignored", doc_repo, path)
                await conn.execute(f"DELETE FROM {self.table} WHERE doc_repo = $1 AND path = $2 AND NOT present "
                                   "AND NOT ignored", doc_repo, path)
        return res.endswith(" 1")

    def ignored_sql(self) -> str:
        """Subconsulta (doc_repo, path) das páginas fora da busca."""
        return f"SELECT doc_repo, path FROM {self.table} WHERE ignored"

    # ------------------------------------------------------------ leitura

    async def get_many(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        keys = list(dict.fromkeys(pairs))
        if not keys:
            return {}
        rows = await self._pool.fetch(
            f"""SELECT d.doc_repo, d.path, d.key, d.type, d.title, d.url, d.attrs FROM {self.table} d
                JOIN unnest($1::text[], $2::text[]) AS k(doc_repo, path) USING (doc_repo, path)
                WHERE d.present""",
            [k[0] for k in keys], [k[1] for k in keys],
        )
        return {(r["doc_repo"], r["path"]): {**dict(r), "attrs": json.loads(r["attrs"])} for r in rows}

    async def listing(self, *, limit: int, offset: int = 0, exclude_types: Sequence[str] = ()) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"""SELECT doc_repo, path, key, type, title, url, attrs FROM {self.table}
                WHERE present AND NOT (type = ANY($3::text[]))
                ORDER BY doc_repo, path LIMIT $1 OFFSET $2""",
            max(1, min(limit, 2000)), max(0, offset), list(exclude_types),
        )
        return [{**dict(r), "attrs": json.loads(r["attrs"])} for r in rows]

    async def inventory(self) -> list[dict[str, Any]]:
        """Todos os documentos presentes (X-Ray)."""
        rows = await self._pool.fetch(
            f"SELECT doc_repo, path, type, title, url, attrs FROM {self.table} WHERE present ORDER BY doc_repo, path")
        return [{**dict(r), "attrs": json.loads(r["attrs"])} for r in rows]

    async def has_present(self) -> bool:
        return bool(await self._pool.fetchval(f"SELECT EXISTS (SELECT 1 FROM {self.table} WHERE present)"))

    async def exists(self, doc_repo: str, path: str) -> bool:
        return bool(await self._pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {self.table} WHERE doc_repo = $1 AND path = $2 AND present)",
            doc_repo, path))

    async def page_indexed(self, doc_repo: str, path: str) -> bool:
        """A página já passou pelo pipeline (está em doc_pages, tabela do CocoIndex)."""
        s = self._settings
        pages = s.qualified(s.doc_pages_table)
        if not await self._pool.fetchval("SELECT to_regclass($1) IS NOT NULL", pages):
            return False
        return bool(await self._pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {pages} WHERE repo = $1 AND path = $2)", doc_repo, path))

    async def api_counts(self, doc_repos: list[str]) -> list[dict[str, Any]]:
        """Operações por domínio e serviço das fontes de API."""
        if not doc_repos:
            return []
        rows = await self._pool.fetch(
            f"""SELECT doc_repo AS repo, attrs->>'domain' AS domain, attrs->>'service' AS service,
                       count(*) AS operations
                FROM {self.table} WHERE present AND type = $2 AND doc_repo = ANY($1::text[])
                GROUP BY 1, 2, 3""",
            doc_repos, API_OPERATION,
        )
        return [dict(r) for r in rows]
