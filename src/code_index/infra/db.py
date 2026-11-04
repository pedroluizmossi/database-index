"""Pool de conexões asyncpg (com pgvector) e migrações do schema do código.

As migrações são arquivos `infra/sql/NNN_*.sql` aplicados em ordem, uma única vez cada,
registrados em `"<schema>".schema_migrations`. A tabela de chunks NÃO é daqui: é gerenciada
pelo CocoIndex.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from pgvector.asyncpg import register_vector

from code_index.settings import Settings

SQL_DIR = Path(__file__).parent / "sql"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def _ensure_vector_extension(dsn: str) -> None:
    """Garante a extensão `vector` antes de registrar o codec (o CocoIndex também a cria)."""
    conn = await asyncpg.connect(dsn)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'"):
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


def _check_identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"nome de schema inválido: {name!r}")
    return name


async def create_pool(settings: Settings) -> asyncpg.Pool:
    await _ensure_vector_extension(settings.database_url)
    return await asyncpg.create_pool(settings.database_url, init=_init_connection, min_size=1)


async def migrate(pool: asyncpg.Pool, settings: Settings) -> list[str]:
    """Aplica as migrações pendentes. Retorna as versões aplicadas agora."""
    schema = _check_identifier(settings.pg_schema)
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serializa migrações concorrentes (dois processos iniciando ao mesmo tempo).
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"code_index.migrate.{schema}")
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            await conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations ('
                "version text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
            )
            done = {r["version"] for r in await conn.fetch(f'SELECT version FROM "{schema}".schema_migrations')}
            for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                version = path.stem
                if version in done:
                    continue
                sql = path.read_text(encoding="utf-8").replace("{schema}", schema)
                await conn.execute(sql)
                await conn.execute(f'INSERT INTO "{schema}".schema_migrations (version) VALUES ($1)', version)
                applied_now.append(version)
    return applied_now


@asynccontextmanager
async def open_pool(settings: Settings) -> AsyncIterator[asyncpg.Pool]:
    """Cria o pool e aplica as migrações; fecha o pool ao sair."""
    pool = await create_pool(settings)
    try:
        await migrate(pool, settings)
        yield pool
    finally:
        await pool.close()
