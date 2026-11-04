"""Estado dos repositórios (tabela code_index.repos), mantido pelo nosso código.

Um repo está "indexado" quando `revision` não é nulo: é a chave que o pipeline usa
para pular repos sem mudanças. Um erro de sync NÃO apaga o que já foi indexado; só
fica registrado em `error` até o próximo sucesso.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from .config import DATABASE_URL, PG_SCHEMA, Repo
from .github import DiscoveredRepo

T = f'"{PG_SCHEMA}".repos'

_DDL = f"""
CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}";
CREATE TABLE IF NOT EXISTS {T} (
    name              text PRIMARY KEY,
    org               text,             -- NULL = repo avulso do repos.toml
    url               text,
    path              text,
    default_branch    text,
    pushed_at         timestamptz,      -- último push segundo a API do GitHub
    size_kb           bigint,
    ignored           boolean NOT NULL DEFAULT false,
    indexed_commit    text,
    revision          text,             -- chave de memo do pipeline (commit ou commit#nonce)
    indexed_pushed_at timestamptz,
    indexed_at        timestamptz,
    error             text,
    discovered_at     timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS repos_org_idx ON {T} (org);
ALTER TABLE {T} ADD COLUMN IF NOT EXISTS language text;
"""


@dataclass
class RepoState:
    name: str
    org: str | None
    url: str | None
    path: str | None
    default_branch: str | None
    pushed_at: datetime | None
    size_kb: int | None
    ignored: bool
    indexed_commit: str | None
    revision: str | None
    indexed_pushed_at: datetime | None
    indexed_at: datetime | None
    error: str | None
    updated_at: datetime
    language: str | None = None

    @property
    def indexed(self) -> bool:
        return self.revision is not None

    @property
    def status(self) -> str:
        if self.ignored:
            return "ignored"
        if self.error:
            return "error"
        return "indexed" if self.indexed else "pending"

    @property
    def changed_upstream(self) -> bool:
        """Para repos de org: houve push depois da última indexação?"""
        return (self.indexed and self.pushed_at is not None
                and (self.indexed_pushed_at is None or self.pushed_at > self.indexed_pushed_at))


async def connect() -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(_DDL)
    return conn


async def all_rows(conn: asyncpg.Connection) -> dict[str, RepoState]:
    rows = await conn.fetch(f"SELECT {', '.join(RepoState.__dataclass_fields__)} FROM {T}")
    return {r["name"]: RepoState(**dict(r)) for r in rows}


async def reconcile_manual(conn: asyncpg.Connection, repos: Iterable[Repo]) -> None:
    """Espelha os [[repo]] do repos.toml na tabela (insere novos, remove os apagados)."""
    repos = list(repos)
    async with conn.transaction():
        for r in repos:
            await conn.execute(
                f"""INSERT INTO {T} (name, url, path, default_branch) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (name) DO UPDATE SET org = NULL, url = $2, path = $3, default_branch = $4""",
                r.name, r.url, r.path, r.branch,
            )
        await conn.execute(f"DELETE FROM {T} WHERE org IS NULL AND NOT (name = ANY($1))", [r.name for r in repos])


async def reconcile_org(conn: asyncpg.Connection, org: str, found: list[DiscoveredRepo]) -> dict[str, int]:
    """Atualiza os repos descobertos de uma org. Repos que sumiram (ou foram filtrados) saem."""
    async with conn.transaction():
        before = {r["name"] for r in await conn.fetch(f"SELECT name FROM {T} WHERE org = $1", org)}
        await conn.executemany(
            f"""INSERT INTO {T} (name, org, url, default_branch, pushed_at, size_kb, language)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (name) DO UPDATE SET org = $2, url = $3, default_branch = $4,
                    pushed_at = $5, size_kb = $6, language = $7
                WHERE {T}.org IS NOT NULL  -- repo avulso de mesmo nome tem precedência""",
            [(d.name, org, d.url, d.default_branch, d.pushed_at, d.size_kb, d.language) for d in found],
        )
        names = [d.name for d in found]
        await conn.execute(f"DELETE FROM {T} WHERE org = $1 AND NOT (name = ANY($2))", org, names)
        manual = {r["name"] for r in await conn.fetch(f"SELECT name FROM {T} WHERE org IS NULL")}
    return {"new": len(set(names) - before - manual), "removed": len(before - set(names)), "total": len(names)}


async def prune_orgs(conn: asyncpg.Connection, keep: list[str]) -> list[str]:
    rows = await conn.fetch(f"DELETE FROM {T} WHERE org IS NOT NULL AND NOT (org = ANY($1)) RETURNING name", keep)
    return [r["name"] for r in rows]


async def delete(conn: asyncpg.Connection, names: list[str]) -> None:
    await conn.execute(f"DELETE FROM {T} WHERE name = ANY($1)", names)


async def set_ignored(conn: asyncpg.Connection, name: str, ignored: bool) -> bool:
    res = await conn.execute(f"UPDATE {T} SET ignored = $2, updated_at = now() WHERE name = $1", name, ignored)
    return res.endswith(" 1")


async def mark_indexed(conn: asyncpg.Connection, name: str, commit: str | None, revision: str,
                       pushed_at: datetime | None) -> None:
    await conn.execute(
        f"""UPDATE {T} SET indexed_commit = $2, revision = $3, indexed_pushed_at = $4,
                indexed_at = now(), error = NULL, updated_at = now() WHERE name = $1""",
        name, commit, revision, pushed_at,
    )


async def mark_error(conn: asyncpg.Connection, name: str, error: str) -> None:
    await conn.execute(f"UPDATE {T} SET error = $2, updated_at = now() WHERE name = $1", name, error[:2000])
