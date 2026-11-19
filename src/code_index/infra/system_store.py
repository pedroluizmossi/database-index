"""Estado do banco para a tela Sistema (admin): versão, tamanho, extensões, maiores tabelas, migrations e fila."""

from __future__ import annotations

from typing import Any

import asyncpg

from ..settings import Settings


class SystemStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._schema = settings.pg_schema

    async def database(self) -> dict[str, Any]:
        s = self._schema
        async with self._pool.acquire() as conn:
            version = await conn.fetchval("SHOW server_version")
            size = await conn.fetchval("SELECT pg_database_size(current_database())")
            exts = await conn.fetch("SELECT extname, extversion FROM pg_extension ORDER BY extname")
            tables = await conn.fetch(
                """SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes,
                          greatest(c.reltuples, 0)::bigint AS rows
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = $1 AND c.relkind IN ('r', 'p')
                   ORDER BY bytes DESC LIMIT 12""", s)
            migrations = await conn.fetch(f'SELECT version, applied_at FROM "{s}".schema_migrations ORDER BY version')
            jobs = await conn.fetch(
                f"""SELECT status, count(*) AS n FROM "{s}".sync_jobs WHERE status IN ('queued', 'running')
                    GROUP BY status""")
        last = migrations[-1] if migrations else None
        return {
            "version": version,
            "bytes": size,
            "schema": s,
            "extensions": {r["extname"]: r["extversion"] for r in exts},
            "tables": [{"name": r["name"], "bytes": r["bytes"], "rows": r["rows"]} for r in tables],
            "migrations": {"count": len(migrations), "last": last["version"] if last else None,
                           "last_applied_at": last["applied_at"].isoformat(timespec="seconds") if last else None},
            "queue": {r["status"]: r["n"] for r in jobs},
        }
