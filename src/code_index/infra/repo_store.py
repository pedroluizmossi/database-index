"""Estado dos repositórios (tabela "<schema>".repos), mantido pelo nosso código.

Um erro de sync fica em `error` sem apagar o que já foi indexado; só some no próximo sucesso.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from datetime import datetime

import asyncpg

from code_index.domain.models import DiscoveredRepo, Repo, RepoState
from code_index.settings import Settings

_COLUMNS = ", ".join(f.name for f in fields(RepoState))


class RepoStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".repos'

    async def all_rows(self) -> dict[str, RepoState]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT {_COLUMNS} FROM {self._t}")
        return {r["name"]: RepoState(**dict(r)) for r in rows}

    async def reconcile_manual(self, repos: Iterable[Repo]) -> None:
        """Espelha os [[repo]] do repos.toml na tabela (insere novos, remove os apagados)."""
        items = list(repos)
        t = self._t
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for r in items:
                    if r.kind == "docs":
                        existing = await conn.fetchrow(f"SELECT org, kind FROM {t} WHERE name = $1 FOR UPDATE", r.name)
                        if existing is not None and (existing["org"] is not None or existing["kind"] != "docs"):
                            raise ValueError(f"fonte de documentação conflita com repo existente: {r.name}")
                    await conn.execute(
                        f"""INSERT INTO {t} (name, url, path, default_branch, kind) VALUES ($1, $2, $3, $4, $5)
                            ON CONFLICT (name) DO UPDATE SET org = NULL, url = $2, path = $3, default_branch = $4,
                                kind = $5""",
                        r.name, r.url, r.path, r.branch, r.kind,
                    )
                await conn.execute(f"DELETE FROM {t} WHERE org IS NULL AND NOT (name = ANY($1))", [r.name for r in items])

    async def reconcile_org(self, org: str, found: list[DiscoveredRepo]) -> dict[str, int]:
        """Atualiza os repos descobertos de uma org. Repos que sumiram (ou foram filtrados) saem.

        Retorna {new, removed, total}. Repo avulso de mesmo nome tem precedência e não conta como novo.
        """
        t = self._t
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                before = {r["name"] for r in await conn.fetch(f"SELECT name FROM {t} WHERE org = $1", org)}
                await conn.executemany(
                    f"""INSERT INTO {t} (name, org, url, default_branch, pushed_at, size_kb, language)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (name) DO UPDATE SET org = $2, url = $3, default_branch = $4,
                            pushed_at = $5, size_kb = $6, language = $7
                        WHERE {t}.org IS NOT NULL  -- repo avulso de mesmo nome tem precedência""",
                    [(d.name, org, d.url, d.default_branch, d.pushed_at, d.size_kb, d.language) for d in found],
                )
                names = [d.name for d in found]
                await conn.execute(f"DELETE FROM {t} WHERE org = $1 AND NOT (name = ANY($2))", org, names)
                manual = {r["name"] for r in await conn.fetch(f"SELECT name FROM {t} WHERE org IS NULL")}
        return {
            "new": len(set(names) - before - manual),
            "removed": len(before - set(names)),
            "total": len(names),
        }

    async def prune_orgs(self, keep: list[str]) -> list[str]:
        """Remove os repos de orgs que não estão em `keep`. Retorna os nomes removidos."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"DELETE FROM {self._t} WHERE org IS NOT NULL AND NOT (org = ANY($1)) RETURNING name", keep
            )
        return [r["name"] for r in rows]

    async def delete(self, names: list[str]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {self._t} WHERE name = ANY($1)", names)

    async def set_ignored(self, name: str, ignored: bool) -> bool:
        async with self._pool.acquire() as conn:
            res = await conn.execute(
                f"UPDATE {self._t} SET ignored = $2, updated_at = now() WHERE name = $1", name, ignored
            )
        return res.endswith(" 1")

    async def mark_indexed(self, name: str, commit: str | None, revision: str, pushed_at: datetime | None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""UPDATE {self._t} SET indexed_commit = $2, revision = $3, indexed_pushed_at = $4,
                        indexed_at = now(), error = NULL, updated_at = now() WHERE name = $1""",
                name, commit, revision, pushed_at,
            )

    async def mark_error(self, name: str, error: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {self._t} SET error = $2, updated_at = now() WHERE name = $1", name, error[:2000]
            )
