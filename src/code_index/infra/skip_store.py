"""Arquivos descartados na indexação, gravados pelo CocoIndex na tabela skipped_files (ver code_flow)."""

from __future__ import annotations

from typing import Any

import asyncpg

from ..settings import Settings


class SkipStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings

    @property
    def _table(self) -> str:
        return self._settings.qualified(self._settings.skipped_table)

    async def available(self) -> bool:
        """A tabela existe (nada foi descartado ainda, ou nenhum sync rodou com esta versão)."""
        return bool(await self._pool.fetchval("SELECT to_regclass($1) IS NOT NULL", self._table))

    async def counts_by_repo(self) -> dict[str, int]:
        if not await self.available():
            return {}
        rows = await self._pool.fetch(f"SELECT repo, COUNT(*) AS n FROM {self._table} GROUP BY repo")
        return {r["repo"]: int(r["n"]) for r in rows}

    async def rows_for(self, *, repo: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if not await self.available():
            return []
        rows = await self._pool.fetch(
            f"""SELECT repo, path, reason, size_bytes FROM {self._table}
                WHERE ($1::text IS NULL OR repo = $1) ORDER BY repo, path LIMIT $2""",
            repo, max(1, min(limit, 2000)),
        )
        return [dict(r) for r in rows]
