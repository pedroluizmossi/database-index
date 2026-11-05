"""Pool de conexões asyncpg (com pgvector) e migrações do schema do código.

As migrações são arquivos `infra/sql/NNN_*.sql` aplicados em ordem, uma única vez cada,
registrados em `"<schema>".schema_migrations`. A tabela de chunks NÃO é daqui: é gerenciada
pelo CocoIndex.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from pgvector.asyncpg import register_vector

from code_index.settings import Settings

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).parent / "sql"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def _ensure_extensions(dsn: str) -> None:
    """Garante `vector` (obrigatória; antes de registrar o codec) e `pg_search` (BM25, opcional).

    `pg_search` só existe na imagem do ParadeDB. Na imagem pgvector comum ela não está disponível
    e a busca por termos usa o full-text do Postgres (ver infra/chunk_store.py).
    """
    conn = await asyncpg.connect(dsn)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'"):
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        available = await conn.fetchval("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search'")
        installed = await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
        if available and not installed:
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
            except asyncpg.PostgresError as e:  # ex.: não carregada em shared_preload_libraries
                log.warning("pg_search disponível mas não pôde ser criada (%s); seguindo sem BM25", e)
    finally:
        await conn.close()


def _check_identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"nome de schema inválido: {name!r}")
    return name


async def create_pool(settings: Settings) -> asyncpg.Pool:
    await _ensure_extensions(settings.database_url)
    return await asyncpg.create_pool(
        settings.database_url,
        init=_init_connection,
        min_size=1,
        # O pg_search avisa (WARNING) quando não usa o plano de agregação dele (ex.: COUNT ... GROUP BY):
        # é só ruído. Vai como parâmetro de conexão porque o pool faz RESET ALL ao devolver a conexão
        # (um SET no init seria perdido). Parâmetro com ponto é aceito mesmo sem a extensão.
        server_settings={"paradedb.planner_warnings": "off"},
    )


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
